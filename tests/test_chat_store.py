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

    def test_corrupt_db_raises_store_error(self, tmp_path) -> None:
        db = tmp_path / "corrupt.db"
        db.write_bytes(b"not a sqlite file")
        store = ChatStore(db)
        with pytest.raises(ChatStoreError):
            store.list_sessions()
