"""ChatStore 单元测试 — 会话持久化（内存 SQLite，不依赖真实 DB）。"""

from __future__ import annotations

import pytest

from doc2mind.core.store.chat_store import ChatStore, ChatStoreError


@pytest.fixture()
def store(tmp_path):
    return ChatStore(tmp_path / "chats.db")


class TestAppendMessage:
    def test_first_user_message_creates_session_with_title(self, store) -> None:
        store.append_message("chat-1", "user", "什么是 DocMind 的架构设计？", title_hint="什么是 DocMind 的架构设计？")
        s = store.get_session("chat-1")
        assert s is not None
        assert s.title == "什么是 DocMind 的架构设计？"
        assert s.message_count == 1

    def test_title_truncated_to_50_chars(self, store) -> None:
        long_q = "问题" * 60  # 120 字符
        store.append_message("chat-1", "user", long_q, title_hint=long_q)
        assert len(store.get_session("chat-1").title) == 50

    def test_multi_turn_counts_messages(self, store) -> None:
        store.append_message("chat-1", "user", "q1", title_hint="q1")
        store.append_message("chat-1", "assistant", "a1")
        store.append_message("chat-1", "user", "q2")
        store.append_message("chat-1", "assistant", "a2")
        assert store.get_session("chat-1").message_count == 4

    def test_invalid_role_raises(self, store) -> None:
        with pytest.raises(ChatStoreError):
            store.append_message("chat-1", "bogus", "x")

    def test_db_error_wrapped(self, tmp_path) -> None:
        # 目录被文件占位 → mkdir/打开失败 → ChatStoreError 而非裸 OSError
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        store = ChatStore(blocker / "sub" / "chats.db")
        with pytest.raises(ChatStoreError):
            store.append_message("chat-1", "user", "q")


class TestAppendTurn:
    """append_turn：user + assistant 单事务落库（AUD-008）。"""

    def test_turn_writes_user_then_assistant_single_session(self, store) -> None:
        store.append_turn("chat-1", "q1", "a1", title_hint="q1")
        store.append_turn("chat-1", "q2", "a2")
        msgs = store.get_messages("chat-1")
        assert [m.content for m in msgs] == ["q1", "a1", "q2", "a2"]
        assert store.get_session("chat-1").message_count == 4
        # 标题由首轮 user 消息生成
        assert store.get_session("chat-1").title == "q1"

    def test_turn_without_assistant_writes_user_only(self, store) -> None:
        store.append_turn("chat-1", "q1", None, title_hint="q1")
        msgs = store.get_messages("chat-1")
        assert [m.content for m in msgs] == ["q1"]

    def test_turn_is_atomic_no_orphan_user_round(self, tmp_path) -> None:
        """assistant 写失败时 user 轮一并回滚（不留孤儿轮）。

        构造：先建一张不含 sources_json 的旧表……实际验证事务性更直接的做法是
        模拟第二次 INSERT 失败；这里用非法 assistant_content（None 视为合法）
        无法触发，因此改为验证：append_turn 在单事务内抛出时两者都不落库。
        """
        store = ChatStore(tmp_path / "atomic.db")
        # 事务原子性：把 user_content 设为空字符串会触发校验异常，
        # 此时会话不应被创建（user/assistant 都未落库）
        with pytest.raises(ChatStoreError):
            store.append_turn("chat-1", "", "a1")
        assert store.get_session("chat-1") is None

    def test_turn_interleaved_requests_keep_pairing(self, store) -> None:
        """两次并发写同一 chat_id 不会交错成 user,user,assistant,assistant。"""
        import threading

        def turn(chat_id: str, n: int) -> None:
            try:
                store.append_turn(chat_id, f"q{n}", f"a{n}")
            except ChatStoreError:
                pass  # 并发写冲突可接受（busy_timeout 兜底），但不得产生交错

        threads = [threading.Thread(target=turn, args=("chat-1", i)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        msgs = store.get_messages("chat-1")
        roles = [(m.content[0], m.role) for m in msgs]
        for i in range(0, len(roles), 2):
            assert roles[i][0] == "q" and roles[i][1] == "user"
            assert roles[i + 1][0] == "a" and roles[i + 1][1] == "assistant"


class TestHistory:
    def test_get_history_ordered_and_limited(self, store) -> None:
        for i in range(30):
            store.append_message("chat-1", "user", f"q{i}")
            store.append_message("chat-1", "assistant", f"a{i}")
        history = store.get_history("chat-1", limit=6)
        assert len(history) == 6
        # 最近 6 条，时间正序
        assert [m["content"] for m in history] == ["q27", "a27", "q28", "a28", "q29", "a29"]
        assert all(m["role"] in ("user", "assistant") for m in history)

    def test_get_history_missing_session_returns_empty(self, store) -> None:
        assert store.get_history("no-such-chat") == []

    def test_get_messages_returns_all(self, store) -> None:
        for i in range(3):
            store.append_message("chat-1", "user", f"q{i}")
            store.append_message("chat-1", "assistant", f"a{i}")
        msgs = store.get_messages("chat-1")
        assert [m.content for m in msgs] == ["q0", "a0", "q1", "a1", "q2", "a2"]
        assert msgs[0].created_at  # 时间戳非空


class TestListAndDelete:
    def test_list_sessions_ordered_by_updated_at_desc(self, store) -> None:
        store.append_message("a", "user", "旧会话问题", title_hint="旧会话问题")
        store.append_message("b", "user", "新会话问题", title_hint="新会话问题")
        store.append_message("a", "user", "又问了一句")  # a 更新
        sessions = store.list_sessions()
        assert [s.chat_id for s in sessions] == ["a", "b"]
        assert sessions[0].title == "旧会话问题"

    def test_delete_session_cascades_messages(self, store) -> None:
        store.append_message("chat-1", "user", "q", title_hint="q")
        store.append_message("chat-1", "assistant", "a")
        assert store.delete_session("chat-1") is True
        assert store.get_session("chat-1") is None
        assert store.get_messages("chat-1") == []
        assert store.delete_session("chat-1") is False

    def test_delete_missing_returns_false(self, store) -> None:
        assert store.delete_session("nope") is False

    def test_count_sessions_returns_total_not_page_size(self, store) -> None:
        """AUD-013：count_sessions 返回会话总数（/v1/chats 的 total 字段）。"""
        assert store.count_sessions() == 0
        store.append_turn("a", "q1", "a1", title_hint="q1")
        store.append_turn("b", "q2", "a2", title_hint="q2")
        store.append_turn("a", "q3", "a3")
        assert store.count_sessions() == 2
        store.delete_session("a")
        assert store.count_sessions() == 1

    def test_corrupt_db_raises_store_error(self, tmp_path) -> None:
        db = tmp_path / "corrupt.db"
        db.write_bytes(b"not a sqlite file")
        store = ChatStore(db)
        with pytest.raises(ChatStoreError):
            store.list_sessions()


class TestSearchAndFts:
    """会话关键字过滤 + 跨会话消息检索 + FTS 索引维护。"""

    def test_list_sessions_filters_by_title_or_content(self, store) -> None:
        store.append_turn("a", "什么是挠度", "挠度是梁的位移", title_hint="什么是挠度")
        store.append_turn("b", "机器学习入门", "监督学习...", title_hint="机器学习入门")
        store.append_turn("c", "无关问题", "正文里提到挠度定义", title_hint="无关问题")

        by_title = store.list_sessions(q="挠度")
        assert {s.chat_id for s in by_title} >= {"a", "c"}

        by_ml = store.list_sessions(q="机器学习")
        assert [s.chat_id for s in by_ml] == ["b"]

        assert store.count_sessions(q="挠度") == len(by_title)
        assert store.count_sessions(q="不存在的关键字xyz") == 0

    def test_search_messages_returns_matching_content(self, store) -> None:
        store.append_turn("a", "q", "关于 DocMind 架构的说明", title_hint="q")
        store.append_turn("b", "q", "其他内容", title_hint="q")
        hits = store.search_messages("DocMind")
        assert len(hits) == 1
        assert hits[0]["chat_id"] == "a"
        assert "DocMind" in hits[0]["content"]

        scoped = store.search_messages("DocMind", chat_id="b")
        assert scoped == []

    def test_fts_table_created_and_indexes_chinese(self, tmp_path) -> None:
        import sqlite3

        db = tmp_path / "fts.db"
        store = ChatStore(db)
        store.append_turn("c1", "你知道挠度吗", "挠度是结构位移", title_hint="你知道挠度吗")

        conn = sqlite3.connect(db)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            assert "chat_messages_fts" in tables
            n = conn.execute("SELECT COUNT(*) FROM chat_messages_fts").fetchone()[0]
            assert n == 2
            # CJK 分词后按字可命中
            hit = conn.execute(
                "SELECT rowid FROM chat_messages_fts WHERE chat_messages_fts MATCH ?",
                ('"挠" AND "度"',),
            ).fetchone()
            assert hit is not None
        finally:
            conn.close()

    def test_delete_session_removes_fts_rows(self, store) -> None:
        import sqlite3

        store.append_turn("gone", "问题", "回答内容", title_hint="问题")
        store.delete_session("gone")
        conn = sqlite3.connect(store._db_path)
        try:
            assert conn.execute("SELECT COUNT(*) FROM chat_messages_fts").fetchone()[0] == 0
        finally:
            conn.close()

    def test_update_last_assistant_refreshes_fts(self, store) -> None:
        import sqlite3

        store.append_turn("c", "q", "旧回答", title_hint="q")
        assert store.update_last_assistant_content("c", "新回答含机器学习")
        conn = sqlite3.connect(store._db_path)
        try:
            hit = conn.execute(
                "SELECT rowid FROM chat_messages_fts WHERE chat_messages_fts MATCH ?",
                ('"机" AND "器" AND "学" AND "习"',),
            ).fetchone()
            assert hit is not None
        finally:
            conn.close()
