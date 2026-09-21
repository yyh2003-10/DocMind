"""Agent 模式流式编排单测（mock LLM + mock 检索）。"""

from __future__ import annotations

import json
from pathlib import Path

from doc2mind.core.agent.runtime.chat_agent import (
    _planner_tools_to_calls,
    _tool_results_to_context_block,
    agent_answer_stream,
)
from doc2mind.core.agent.runtime.types import ToolCall, ToolResult, ToolStatus


class _FakePlan:
    query_type = "question"
    creative_mode = None
    enabled_tools = ["knowledge_base"]
    analysis = "知识问答"
    is_greeting = False
    degraded = False


def test_planner_tools_mapping():
    calls = _planner_tools_to_calls(_FakePlan(), "什么是GPT", 5)
    assert calls and calls[0].tool_id == "kb_search"
    assert calls[0].arguments["query"] == "什么是GPT"


def test_tool_results_context_block():
    results = [
        ToolResult(
            call_id="c1",
            tool_id="kb_search",
            status=ToolStatus.OK,
            summary="hit",
            data={"hits": [{"source": "a.md", "content": "要点"}]},
        ),
        ToolResult(call_id="c2", tool_id="write_workspace_file", status=ToolStatus.DENIED, error="no"),
    ]
    block = _tool_results_to_context_block(results)
    assert "a.md" in block
    assert "失败" in block


class _FakeChunk:
    def __init__(self, content, source):
        self.content = content
        self.source = source
        self.heading = None
        self.page = None


class _FakeHit:
    def __init__(self, content, source, score=0.9):
        self.chunk = _FakeChunk(content, source)
        self.score = score
        self.rerank_score = score


class _FakeStore:
    def bm25_search(self, query, top_k=10, collection=None):
        return [(_FakeHit("GPT 是生成式预训练模型", "kb/gpt.md"), 1.0)]

    def vector_search(self, query_vec, top_k=10, collection=None):
        return []


class _FakeEmbedder:
    def embed_query(self, text):
        return [0.1, 0.2, 0.3]

    def embed_texts(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeLLM:
    provider = "openai"
    model_name = "fake-model"
    last_truncated = False

    def stream_chat_tagged(self, messages, max_tokens=None, timeout=None, stop_event=None):
        # 带句读，便于 OutputSanitizer 按句吐出
        yield "token", "这是关于 GPT 的 Agent 回答，"
        yield "token", "结论：基于知识库要点。"


def test_agent_answer_stream_emits_frames(tmp_path, monkeypatch):
    import doc2mind.core.agent.runtime.chat_agent as ca
    from doc2mind.core.config import Settings

    monkeypatch.setattr(ca, "Retriever", lambda store, embedder, reranker=None: _SimpleRetriever())
    monkeypatch.setattr(ca, "plan_with_llm", lambda *a, **k: _FakePlan())

    class _SimpleRetriever:
        def search(self, q, collection=None, top_k=5):
            return [_FakeHit("GPT 要点来自知识库", "kb/gpt.md")], None

    s = Settings()
    object.__setattr__(s, "db_path", tmp_path / "doc2mind.db") if hasattr(s, "__dataclass_fields__") else None
    try:
        s.db_path = tmp_path / "doc2mind.db"
    except Exception:
        pass
    for attr, val in (
        ("llm_provider", "openai"),
        ("llm_model", "fake-model"),
        ("llm_max_tokens", 2048),
        ("rag_top_k", 5),
        ("agent_file_write_policy", "always_allow_workspace"),
    ):
        try:
            setattr(s, attr, val)
        except Exception:
            pass

    frames = list(
        agent_answer_stream(
            "什么是GPT",
            collection="default",
            top_k=3,
            chat_id="chat-agent-test",
            settings=s,
            llm_client=_FakeLLM(),
            store=object(),
            embedder=object(),
        )
    )
    types = []
    done = None
    tokens = []
    for line in frames:
        obj = json.loads(line)
        if "type" in obj:
            types.append(obj["type"])
        if obj.get("token"):
            tokens.append(obj["token"])
        if obj.get("done"):
            done = obj
    assert done is not None
    assert done.get("mode") == "agent"
    assert "".join(tokens)
    assert "thinking" in types or "agent_plan" in types
    note_root = Path(done["workspace_root"])
    assert note_root.exists()
    assert done.get("artifacts")
    assert "kb_search" in (done.get("tools_used") or [])
    # P1 修复：kb 命中应进入 sources，而非空列表
    assert isinstance(done.get("sources"), list)
    assert done["sources"] and done["sources"][0].get("source") == "kb/gpt.md"


def test_workspace_root_sanitizes_chat_id(tmp_path):
    from doc2mind.core.agent.runtime.chat_agent import _workspace_root_for
    from doc2mind.core.config import Settings

    s = Settings()
    s.db_path = tmp_path / "d.db"
    evil = _workspace_root_for(s, "..\\..\\Windows\\evil")
    assert "Windows" not in str(evil) or "workspaces" in str(evil)
    # 必须落在 workspaces 下的消毒名
    assert evil.parent.name == "workspaces"
    assert ".." not in evil.name


def test_agent_mode_continue_preferred_over_rag():
    from doc2mind.server.http import ChatRequest

    req = ChatRequest(query="继续写", continue_writing=True, agent_mode=True)
    assert req.is_agent_mode() is True
    assert req.continue_writing is True
    # HTTP 层：continue_writing 时不应进入 agent 分支（由调用方判断）
    assert not (req.is_agent_mode() and not req.continue_writing)
