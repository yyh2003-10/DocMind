"""续写合并与 ChatStore 更新的回归测试。"""

from __future__ import annotations

from pathlib import Path

from doc2mind.core.agent.prompt_policy import build_continue_query
from doc2mind.core.rag import (
    _CHAT_SESSIONS,
    _HISTORY_LOCK,
    _append_turn,
    _merge_continue_into_last_assistant,
)
from doc2mind.core.store.chat_store import ChatStore


def test_merge_continue_updates_memory_history(tmp_path):
    cid = "chat-merge-1"
    with _HISTORY_LOCK:
        _CHAT_SESSIONS[cid] = [
            {"role": "user", "content": "写长报告"},
            {"role": "assistant", "content": "第一段…"},
        ]
    _merge_continue_into_last_assistant(cid, "第二段…", db_path=None)
    hist = _CHAT_SESSIONS[cid]
    assert len(hist) == 2
    assert hist[-1]["role"] == "assistant"
    assert hist[-1]["content"] == "第一段…\n\n第二段…"
    with _HISTORY_LOCK:
        _CHAT_SESSIONS.pop(cid, None)


def test_chat_store_update_last_assistant_not_duplicate(tmp_path):
    db = tmp_path / "chats.db"
    store = ChatStore(db)
    cid = "chat-db-1"
    store.append_turn(cid, "问题", "旧回答", title_hint="问题")
    ok = store.update_last_assistant_content(cid, "旧回答\n\n续写")
    assert ok is True
    hist = store.get_history(cid, limit=20)
    assistants = [m for m in hist if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"] == "旧回答\n\n续写"
    # 不应出现 [continue] 用户轮
    users = [m for m in hist if m.get("role") == "user"]
    assert all(m.get("content") != "[continue]" for m in users)


def test_merge_continue_db_update_path(tmp_path):
    db = tmp_path / "chats2.db"
    # 绑定 rag 模块使用的 store 缓存
    import doc2mind.core.rag as rag

    rag._CHAT_STORES.clear()
    cid = "chat-db-2"
    store = rag._get_chat_store(db)
    assert store is not None
    store.append_turn(cid, "Q", "A部分", title_hint="Q")
    with _HISTORY_LOCK:
        _CHAT_SESSIONS[cid] = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A部分"},
        ]
    _merge_continue_into_last_assistant(cid, "B部分", db_path=db)
    hist = store.get_history(cid, limit=20)
    assistants = [m for m in hist if m.get("role") == "assistant"]
    assert len(assistants) == 1, hist
    assert "B部分" in assistants[0]["content"]
    assert "A部分" in assistants[0]["content"]
    assert not any(m.get("content") == "[continue]" for m in hist)
    with _HISTORY_LOCK:
        _CHAT_SESSIONS.pop(cid, None)
    rag._CHAT_STORES.clear()


def test_build_continue_query_default():
    assert "续写" in build_continue_query("继续写") or "继续" in build_continue_query("继续写")
