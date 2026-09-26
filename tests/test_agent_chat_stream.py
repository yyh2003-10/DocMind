"""Agent 模式流式编排单测（mock LLM + mock 检索）。"""

from __future__ import annotations

import json
from pathlib import Path

from doc2mind.core.agent.runtime.chat_agent import (
    _planner_tools_to_calls,
    _tool_hits_to_source_dicts,
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
    # 默认未开联网：不应自动插入 web_search
    assert all(c.tool_id != "web_search" for c in calls)


def test_planner_tools_mapping_includes_web_search():
    class _WebPlan(_FakePlan):
        enabled_tools = ["knowledge_base", "web_search"]

    calls = _planner_tools_to_calls(
        _WebPlan(), "什么是挠度", 5, enable_web_search=True, web_search_mode="deep"
    )
    ids = [c.tool_id for c in calls]
    assert "kb_search" in ids
    assert "web_search" in ids
    web = next(c for c in calls if c.tool_id == "web_search")
    assert web.arguments["mode"] == "deep"
    # 用户开关打开时，即使 planner 漏了 web_search 也必须补上
    calls2 = _planner_tools_to_calls(_FakePlan(), "什么是挠度", 5, enable_web_search=True)
    assert any(c.tool_id == "web_search" for c in calls2)


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


def test_tool_results_context_block_web_and_sources():
    results = [
        ToolResult(
            call_id="w1",
            tool_id="web_search",
            status=ToolStatus.OK,
            summary="ok",
            data={
                "mode": "deep",
                "results": [
                    {
                        "title": "挠度定义",
                        "url": "https://example.com/d",
                        "domain": "example.com",
                        "content": "挠度是结构构件在外力下的线位移",
                        "snippet": "挠度定义",
                        "relevance_score": 0.8,
                        "evidence_level": "交叉印证",
                        "content_fetched": True,
                    }
                ],
            },
        ),
    ]
    block = _tool_results_to_context_block(results)
    assert "实时联网检索" in block
    assert "挠度" in block
    assert "https://example.com/d" in block

    class _Loop:
        tool_results = results

    sources = _tool_hits_to_source_dicts(_Loop())
    assert sources and sources[0]["source_type"] == "web"
    assert sources[0]["url"] == "https://example.com/d"


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


def test_agent_answer_stream_emits_web_search_when_enabled(tmp_path, monkeypatch):
    """联网开启时 Agent 路径应规划并执行 web_search（对标多轮搜索短板补齐）。"""
    import doc2mind.core.agent.runtime.chat_agent as ca
    from doc2mind.core.config import Settings
    from doc2mind.core.search import web_search as ws_mod

    monkeypatch.setattr(ca, "Retriever", lambda store, embedder, reranker=None: _SimpleRetriever())
    monkeypatch.setattr(ca, "plan_with_llm", lambda *a, **k: _FakePlan())

    class _SimpleRetriever:
        def search(self, q, collection=None, top_k=5):
            return [_FakeHit("库内要点", "kb/a.md")], None

    class _FakeWebService:
        def set_searxng_bases(self, bases):
            pass

        def search(self, query, **kwargs):
            class _R:
                title = "Deflection of beams"
                url = "https://example.com/beam"
                snippet = "deflection definition"
                content = "挠度是梁在外力下的竖向位移"
                domain = "example.com"
                source_name = "Web"
                relevance_score = 0.85
                evidence_level = "单一来源"
                content_fetched = True
                published_at = None
                corroborated_by = 0

            return [_R()]

    monkeypatch.setattr(ws_mod, "get_web_search_service", lambda: _FakeWebService())

    s = Settings()
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
        ("agent_native_tool_calling", False),
    ):
        try:
            setattr(s, attr, val)
        except Exception:
            pass

    frames = list(
        agent_answer_stream(
            "什么是挠度",
            collection="default",
            top_k=3,
            chat_id="chat-agent-web",
            settings=s,
            llm_client=_FakeLLM(),
            store=object(),
            embedder=object(),
            enable_web_search=True,
            web_search_mode="deep",
        )
    )
    done = None
    agent_plan = None
    for line in frames:
        obj = json.loads(line)
        if obj.get("done"):
            done = obj
        if obj.get("type") == "agent_plan":
            agent_plan = obj
    assert agent_plan is not None
    assert "web_search" in (agent_plan.get("planned_tools") or [])
    assert agent_plan.get("web_search_mode") == "deep"
    assert done is not None
    assert "web_search" in (done.get("tools_used") or [])
    web_srcs = [x for x in (done.get("sources") or []) if x.get("source_type") == "web"]
    assert web_srcs
    assert web_srcs[0].get("url", "").startswith("https://")


def test_agent_mode_continue_preferred_over_rag():
    from doc2mind.server.http import ChatRequest

    req = ChatRequest(query="继续写", continue_writing=True, agent_mode=True)
    assert req.is_agent_mode() is True
    assert req.continue_writing is True
    # HTTP 层：continue_writing 时不应进入 agent 分支（由调用方判断）
    assert not (req.is_agent_mode() and not req.continue_writing)
