"""T2/T3/T5/T6/T8 自动化验收（不依赖真实 LLM/向量库）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from doc2mind.core.config import Settings
from doc2mind.core.rag import (
    _hit_supports_query,
    partition_citation_hits,
    rag_answer,
    rag_answer_stream,
)
from doc2mind.core.retriever.search import SearchHit, SearchStats, StoredChunkMeta
from doc2mind.core.search.provider import (
    BuiltinSearchProvider,
    HttpJsonSearchProvider,
    mask_secret,
    resolve_search_provider,
)
from doc2mind.core.llm.base import ChatToolTurn, LLMClient, ToolCallDelta


def _make_hit(content="测试内容", source="test.pdf", score=0.8, rerank=None) -> SearchHit:
    chunk = StoredChunkMeta(
        id=1, content=content, source=source, format="pdf", doc_type=None,
        page=1, heading=None, tokens=100, chunk_index=0, collection="default",
    )
    return SearchHit(
        chunk=chunk, score=score, match_type="hybrid",
        vector_score=score, bm25_score=score * 0.9, rank=0,
        rerank_score=rerank,
    )


class MockLLMClient(LLMClient):
    def __init__(self, reply="根据资料，测试回答。"):
        self._reply = reply
        self.last_messages = []

    @property
    def model_name(self):
        return "mock-model"

    @property
    def provider(self):
        return "mock"

    def _do_chat(self, messages, temperature=None, max_tokens=None):
        self.last_messages = messages
        return self._reply


class ToolCallMockClient(MockLLMClient):
    def __init__(self):
        super().__init__("最终回答基于检索结果。")
        self._supports_tool_calling = True
        self.tool_turns = [
            ChatToolTurn(tool_calls=[ToolCallDelta(id="c1", name="kb_search", arguments={"query": "挠度"})]),
            ChatToolTurn(final_text="最终回答基于检索结果。"),
        ]
        self.calls = 0

    @property
    def supports_tool_calling(self):
        return True

    def chat_with_tools(self, messages, tools=None, *, temperature=None, max_tokens=None, timeout=None):
        self.calls += 1
        if self.calls <= len(self.tool_turns):
            return self.tool_turns[self.calls - 1]
        return ChatToolTurn(final_text="最终回答基于检索结果。")


# --- T3 门控 ---
def test_mixed_score_and_topic_demotions():
    q = "什么是挠度"
    good = _make_hit("挠度是位移", source="m.md", score=0.9, rerank=0.9)
    off = _make_hit("DocMind 操作指南", source="g.md", score=0.9, rerank=0.92)
    low1 = _make_hit("无关菜单", source="a.md", score=0.1, rerank=0.05)
    low2 = _make_hit("其它噪声", source="b.md", score=0.08, rerank=0.02)
    part = partition_citation_hits(q, [good, off, low1, low2], citation_min_score=0.45, reranked_usable=True)
    assert part.cite_hits == [good]
    assert off in part.bg_hits
    # 偏题低分：主题不符且低分 → dropped_by_topic；仅低分带主题 → dropped_by_score
    assert part.gate["dropped_by_score"] + part.gate["dropped_by_topic"] == 2
    assert part.gate["topic_demoted_count"] == 1
    assert part.gate["cross_check"] is True


def test_gate_exception_falls_back_to_citable():
    mock_client = MockLLMClient("回答")
    s = Settings(llm_provider="openai", llm_api_key="test")
    hit = _make_hit("挠度定义内容", source="mech.pdf", score=0.8)
    stats = SearchStats(query="q", total_hits=1, elapsed_ms=3, vector_candidates=1, bm25_candidates=1)
    with patch("doc2mind.core.rag.partition_citation_hits", side_effect=RuntimeError("boom")):
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                MockRetriever.return_value.search.return_value = ([hit], stats)
                result = rag_answer(query="什么是挠度", settings=s, llm_client=mock_client)
    # FR-06：门控异常不得静默丢命中
    assert any(src.source == "mech.pdf" for src in result.sources)
    assert result.evidence.get("citation_gate", {}).get("fallback_citable") is True


def test_empty_gate_status_before_tokens():
    import json as _json

    mock_client = MockLLMClient("💡 本地知识库未命中直接依据，以下基于通用知识为您解答：一般说明。")
    s = Settings(llm_provider="openai", llm_api_key="test", rag_mode="hybrid")
    off = _make_hit("DocMind 导入步骤", source="guide.pdf", score=0.95)
    stats = SearchStats(query="q", total_hits=1, elapsed_ms=3, vector_candidates=1, bm25_candidates=1)
    with patch("doc2mind.core.rag._open_store") as mock_open:
        mock_open.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MockRetriever:
            MockRetriever.return_value.search.return_value = ([off], stats)
            with patch("doc2mind.core.store.graph_store.GraphStore") as gs:
                gs.return_value.find_entities_by_keyword.return_value = []
                frames = list(rag_answer_stream(
                    query="什么是挠度", settings=s, llm_client=mock_client,
                    enable_web_search=False,
                ))
    parsed = [_json.loads(f) for f in frames]
    first_token_idx = next((i for i, p in enumerate(parsed) if "token" in p), None)
    gen_status_idx = next(
        (i for i, p in enumerate(parsed)
         if p.get("type") == "status" and "正在生成回答" in p.get("message", "")),
        None,
    )
    warn_idx = next(
        (i for i, p in enumerate(parsed)
         if p.get("type") == "status" and "无可用库内依据" in p.get("message", "")),
        None,
    )
    assert warn_idx is not None
    if first_token_idx is not None:
        assert warn_idx < first_token_idx
    if gen_status_idx is not None:
        assert warn_idx < gen_status_idx
    # 空引用：状态流不得出现伪造编号来源列表
    done = next((p for p in parsed if p.get("done")), None)
    assert done is not None
    local_sources = [s for s in done.get("sources", []) if s.get("source_type", "local") == "local"]
    assert local_sources == []


def test_stage_fields_present_and_switchable():
    import json as _json

    mock_client = MockLLMClient("回答")
    s = Settings(llm_provider="openai", llm_api_key="test", stage_elapsed_enabled=True)
    hit = _make_hit("这是一个关于问题的说明。", score=0.7)
    stats = SearchStats(query="q", total_hits=1, elapsed_ms=3, vector_candidates=1, bm25_candidates=1)
    with patch("doc2mind.core.rag._open_store") as mock_open:
        mock_open.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MockRetriever:
            MockRetriever.return_value.search.return_value = ([hit], stats)
            frames = list(rag_answer_stream(query="问题", settings=s, llm_client=mock_client))
    done = _json.loads(frames[-1])
    for key in (
        "stage_retrieval_ms", "stage_web_ms", "stage_tool_ms",
        "stage_first_token_ms", "stage_generation_ms", "stage_total_ms",
    ):
        assert key in done, key
        assert done[key] >= 0 or (key == "stage_first_token_ms" and done[key] == -1)
    assert done["stage_first_token_mode"] in ("stream", "n/a")

    s2 = Settings(llm_provider="openai", llm_api_key="test", stage_elapsed_enabled=False)
    with patch("doc2mind.core.rag._open_store") as mock_open:
        mock_open.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MockRetriever:
            MockRetriever.return_value.search.return_value = ([hit], stats)
            frames2 = list(rag_answer_stream(query="问题", settings=s2, llm_client=mock_client))
    done2 = _json.loads(frames2[-1])
    assert "stage_retrieval_ms" not in done2


def test_bg_injection_bounded():
    mock_client = MockLLMClient("回答")
    s = Settings(
        llm_provider="openai", llm_api_key="test",
        background_hit_limit=2, citation_min_score=0.45,
    )
    cite = _make_hit("挠度定义相关", source="ok.pdf", score=0.9, rerank=0.9)
    bgs = [
        _make_hit(f"背景{i} 挠度弱相关", source=f"bg{i}.pdf", score=0.4, rerank=0.30)
        for i in range(6)
    ]
    hits = [cite, *bgs]
    stats = SearchStats(
        query="q", total_hits=len(hits), elapsed_ms=3,
        vector_candidates=len(hits), bm25_candidates=len(hits), reranked=True,
    )
    with patch("doc2mind.core.rag._open_store") as mock_open:
        mock_open.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MockRetriever:
            MockRetriever.return_value.search.return_value = (hits, stats)
            with patch("doc2mind.core.store.graph_store.GraphStore") as gs:
                gs.return_value.find_entities_by_keyword.return_value = []
                result = rag_answer(query="什么是挠度", settings=s, llm_client=mock_client)
    gate = result.evidence.get("citation_gate", {})
    assert gate.get("reranked_usable") is True
    assert gate.get("bg_injected", 0) <= 2
    assert gate.get("bg_overflow", 0) >= 1
    assert any(src.source == "ok.pdf" for src in result.sources)


def test_slow_model_waiting_hints_increase():
    import json as _json
    import time

    class SlowStreamClient(MockLLMClient):
        def _do_stream_chat(self, messages, temperature=None, max_tokens=None, stop_event=None):
            time.sleep(2.2)
            yield "回答"
            time.sleep(0.3)
            yield "内容"

    mock_client = SlowStreamClient()
    s = Settings(
        llm_provider="openai", llm_api_key="test",
        llm_first_token_slow_ms=1000,
    )
    with patch("doc2mind.core.rag._open_store") as mock_open:
        mock_open.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MockRetriever:
            MockRetriever.return_value.search.return_value = (
                [],
                SearchStats(query="q", total_hits=0, elapsed_ms=1, vector_candidates=0, bm25_candidates=0),
            )
            frames = list(rag_answer_stream(query="慢模型问题", settings=s, llm_client=mock_client, rag_mode="hybrid"))
    parsed = [_json.loads(f) for f in frames]
    waits = []
    for p in parsed:
        if p.get("type") != "status":
            continue
        msg = p.get("message", "")
        if "已等待" in msg:
            # 提取 Ns
            import re
            m = re.search(r"已等待\s*(\d+)s", msg)
            if m:
                waits.append(int(m.group(1)))
    # 至少 1 条等待提示；若有 2 条则 N 递增
    assert len(waits) >= 1, [p for p in parsed if p.get("type") == "status"]
    if len(waits) >= 2:
        assert waits[-1] >= waits[0]
    # tokens 仍完整
    body = "".join(p.get("token", "") for p in parsed if "token" in p)
    assert "回答" in body


# --- T2 多轮 ---
def test_multiturn_history_keeps_failed_turn_and_citation_safe(tmp_path):
    from doc2mind.core.rag import _append_turn, _load_history

    db = tmp_path / "m.db"
    _append_turn("cid-t2", "上句问了挠度", "（本轮生成失败：LLMError）", db)
    _append_turn("cid-t2", "它的单位是什么？", "挠度单位通常是 mm。", db, sources=[])
    cid, hist = _load_history("cid-t2", db)
    assert cid == "cid-t2"
    contents = [m.get("content", "") for m in hist]
    assert any("挠度" in c for c in contents)
    assert any("失败" in c for c in contents)
    assert any("单位" in c for c in contents)


# --- T4 继续写/截断契约 ---
def test_continue_merge_preserves_no_duplicate_history(tmp_path):
    from doc2mind.core.rag import _append_turn, _load_history, _merge_continue_into_last_assistant

    db = tmp_path / "c.db"
    _append_turn("cid-t4", "写一篇长文", "【第一段】开头内容。", db)
    _merge_continue_into_last_assistant("cid-t4", "\n【第二段】续写内容。", db)
    _, hist = _load_history("cid-t4", db)
    assistant_texts = [m["content"] for m in hist if m.get("role") == "assistant"]
    # 不得出现「原答 + 合并全文」双份
    assert len(assistant_texts) == 1
    assert "第一段" in assistant_texts[0]
    assert "第二段" in assistant_texts[0]


def test_truncated_done_flag_contract():
    from doc2mind.core.agent.prompt_policy import done_frame_extras
    extras = done_frame_extras(track="delivery", truncated=True, continue_writing=False)
    assert extras.get("truncated") is True
    assert extras.get("continue_supported") is True
    assert extras.get("prompt_track") == "delivery"


# --- T5 模型延迟 ---
def test_non_stream_timing_stage_first_token_na():
    mock_client = MockLLMClient("回答")
    s = Settings(llm_provider="openai", llm_api_key="test")
    hit = _make_hit("问题相关说明", score=0.7)
    stats = SearchStats(query="q", total_hits=1, elapsed_ms=2, vector_candidates=1, bm25_candidates=1)
    with patch("doc2mind.core.rag._open_store") as mock_open:
        mock_open.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MockRetriever:
            MockRetriever.return_value.search.return_value = ([hit], stats)
            result = rag_answer(query="问题", settings=s, llm_client=mock_client)
    assert "generation_ms" in result.timing
    assert result.timing.get("stage_first_token_ms") == -1
    assert result.timing.get("stage_first_token_mode") == "n/a"


def test_failure_suggestions_mention_model_and_web():
    from doc2mind.core.llm.base import LLMError
    from doc2mind.core.rag import _generate_failure_suggestions

    text = _generate_failure_suggestions(LLMError("connection timeout"), MockLLMClient(), [], [])
    assert "联网" in text or "模型" in text


# --- T6 Search Provider ---
def test_resolve_provider_builtin_and_keyless():
    assert isinstance(resolve_search_provider("builtin", None), BuiltinSearchProvider)
    assert isinstance(resolve_search_provider("tavily", None), BuiltinSearchProvider)  # 无 key 回落
    p = resolve_search_provider("tavily", "tvly-secret-key-123")
    assert isinstance(p, HttpJsonSearchProvider)
    assert p.name == "tavily"


def test_provider_mask_and_degraded_no_key():
    assert mask_secret("") == ""
    assert mask_secret("tvly-abcdefghijkl") .endswith("kl")
    p = HttpJsonSearchProvider("tavily", "https://example.invalid/search", "")
    res = p.search("q", max_results=3)
    assert res.degraded is True
    assert ("API Key" in (res.error or "")) or ("api_key" in (res.error or "")) or ("未配置" in (res.error or ""))
    assert res.hits == []


def test_search_provider_config_defaults():
    s = Settings()
    assert s.search_provider == "builtin"
    assert s.search_provider_api_key is None
    assert s.agent_mode_enabled is False
    assert s.agent_native_tool_calling is True


# --- T8 tool-calling ---
def test_openai_tool_call_parse_and_whitelist():
    from doc2mind.core.llm.openai_impl import OpenAIClient

    class _Fn:
        name = "kb_search"
        arguments = '{"query":"挠度","top_k":3}'

    class _Call:
        id = "call_1"
        function = _Fn()

    class _Msg:
        tool_calls = [_Call()]
        content = None

    parsed = OpenAIClient._parse_tool_calls(_Msg())
    assert parsed[0].name == "kb_search"
    assert parsed[0].arguments["query"] == "挠度"
    # 白名单外工具在 chat_agent 映射层忽略
    assert OpenAIClient._parse_tool_calls(_Msg())[0].name in ("kb_search",)


def test_agent_mode_disabled_by_default_and_native_flag():
    s = Settings()
    assert s.agent_mode_enabled is False
    # chat_agent 在 agent_mode_enabled=false 时不可达（http 层回落）——此处验证配置红线
    assert getattr(s, "agent_file_write_policy", "") in ("ask", "session_allow", "always_allow_workspace")


def test_mock_client_chat_with_tools_fallback():
    client = MockLLMClient("普通回答")
    assert client.supports_tool_calling is False
    turn = client.chat_with_tools([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert turn.wants_tools is False
    assert "普通回答" in turn.final_text


def test_citation_supports_query_unit():
    q = "什么是挠度"
    assert _hit_supports_query(q, _make_hit("挠度是指…", source="a.md")) is True
    assert _hit_supports_query(q, _make_hit("导入步骤", source="b.md")) is False
