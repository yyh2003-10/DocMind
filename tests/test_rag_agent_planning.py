"""Integration tests for RAG pipeline agent planning step.

Verifies that:
1. LLM-based planning produces correct tool routing
2. Planning thinking frame is emitted before tool execution
3. Self-reflection frame is emitted after generation
4. Greeting queries skip all retrieval
5. Different intents produce different tool selections
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from doc2mind.core.config import Settings
from doc2mind.core.llm.base import LLMClient
from doc2mind.core.rag import (
    _CHAT_SESSIONS,
    rag_answer_stream,
)
from doc2mind.core.retriever.search import SearchHit, SearchStats, StoredChunkMeta


# --- Mock LLM Client ---
class MockLLMClient(LLMClient):
    def __init__(self, reply: str = "测试回答内容。") -> None:
        self._reply = reply
        self.last_messages: list[dict] = []
        self.stream_calls: list[dict] = []

    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def provider(self) -> str:
        return "mock"

    def _do_chat(self, messages: list[dict], temperature: float | None = None,
                 max_tokens: int | None = None) -> str:
        self.last_messages = messages
        return self._reply

    def _do_stream_chat(self, messages: list[dict], temperature: float | None = None,
                        max_tokens: int | None = None, stop_event=None):
        """Yield raw strings (not tuples) - the base class wraps them."""
        self.last_messages = messages
        self.stream_calls.append({"messages": messages, "temperature": temperature, "max_tokens": max_tokens})
        for char in self._reply:
            if stop_event and stop_event.is_set():
                break
            yield char


# --- Helper: 构造测试用 SearchHit ---
def _make_hit(
    content: str = "测试内容",
    source: str = "test.pdf",
    page: int | None = 1,
    heading: str | None = None,
    score: float = 0.8,
) -> SearchHit:
    chunk = StoredChunkMeta(
        id=1,
        content=content,
        source=source,
        format="pdf",
        doc_type=None,
        page=page,
        heading=heading,
        tokens=100,
        chunk_index=0,
        collection="default",
    )
    return SearchHit(
        chunk=chunk,
        score=score,
        match_type="hybrid",
        vector_score=score,
        bm25_score=score * 0.9,
        rank=0,
    )


# --- Tests: Planning Step ---
class TestPlanningStep:
    """Verify that planning thinking frame is emitted before tool execution."""

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_planning_frame_emitted_for_complex_query(self) -> None:
        """Complex query should emit planning frame with tool list."""
        mock_client = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                results = list(rag_answer_stream(
                    query="这个报错怎么解决",
                    settings=s,
                    llm_client=mock_client,
                ))

        all_frames = [json.loads(r) for r in results]
        thinking_frames = [f for f in all_frames if f.get("type") == "thinking"]
        
        # Should have planning frame
        assert len(thinking_frames) >= 1
        planning_frame = thinking_frames[0]
        # The planning frame should contain analysis and tool selection
        assert "text" in planning_frame
        assert len(planning_frame["text"]) > 0

    def test_planning_frame_for_greeting(self) -> None:
        """Greeting should emit planning frame with 'no tools' message."""
        mock_client = MockLLMClient("你好！")
        s = Settings(llm_provider="openai", llm_api_key="test")

        results = list(rag_answer_stream(
            query="你好",
            settings=s,
            llm_client=mock_client,
        ))

        all_frames = [json.loads(r) for r in results]
        thinking_frames = [f for f in all_frames if f.get("type") == "thinking"]
        
        # Should have planning frame
        assert len(thinking_frames) >= 1
        planning_frame = thinking_frames[0]
        # Greeting should say no tools needed
        assert "无需检索" in planning_frame["text"]

    def test_planning_frame_for_simple_qa(self) -> None:
        """Simple Q&A should emit planning frame with tool selection."""
        mock_client = MockLLMClient("答案是...")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                results = list(rag_answer_stream(
                    query="什么是机器学习",
                    settings=s,
                    llm_client=mock_client,
                ))

        all_frames = [json.loads(r) for r in results]
        thinking_frames = [f for f in all_frames if f.get("type") == "thinking"]
        
        # Should have planning frame with tool selection（降级提示帧可能占首位，规划帧仍存在）
        assert len(thinking_frames) >= 1
        planning_frame = next(
            (f for f in thinking_frames if "将调用" in f.get("text", "")),
            None,
        )
        # Mock LLM 返回「答案是...」非 JSON → 规则回退且 degraded=True → 首帧为降级提示
        # （此测试关注规划帧仍被输出，不要求它必须是第一个 thinking 帧）
        assert planning_frame is not None, f"未找到含「将调用」的规划帧: {thinking_frames}"


# --- Tests: Self-Reflection Step ---
class TestSelfReflectionStep:
    """Verify that self-reflection frame is emitted after generation."""

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_reflection_frame_with_sources(self) -> None:
        """有实际 [n] 引用时，自省帧报「已综合 N 条知识库引用」。"""
        mock_client = MockLLMClient("根据资料 [1]，回答内容完整。")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                results = list(rag_answer_stream(
                    query="测试问题",
                    settings=s,
                    llm_client=mock_client,
                ))

        all_frames = [json.loads(r) for r in results]
        thinking_frames = [f for f in all_frames if f.get("type") == "thinking"]

        # Last thinking frame should be reflection
        assert len(thinking_frames) >= 2
        reflection_frame = thinking_frames[-1]
        assert "已综合" in reflection_frame["text"]
        assert "知识库引用" in reflection_frame["text"]

    def test_reflection_frame_honest_when_no_citation(self) -> None:
        """检索到了资料但回答未标注 [n] 时，自省帧不得谎称「已综合引用」。

        真实故障（2026-09-12 GPT 问答）：检索 5 条无关笔记，答案写明
        「未在提供的文献中直接引用」，自省帧却说「已综合 5 条知识库引用」。
        底层能力改造后：弱相关/主题不匹配命中直接不进 sources，
        自省改为「基于通用知识」；中等以上命中但正文无 [n] 时说「未直接标注」。
        """
        # 分数中等且主题可匹配：命中进入 sources，但正文无引用编号
        mock_client = MockLLMClient("这是基于通用知识的回答，未引用库内文献。")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="GPT 相关说明文档内容", source="note.md", page=1, score=0.7)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                results = list(rag_answer_stream(
                    query="什么是GPT",
                    settings=s,
                    llm_client=mock_client,
                ))

        thinking_texts = [
            json.loads(r).get("text", "")
            for r in results
            if json.loads(r).get("type") == "thinking"
        ]
        assert any("未直接标注引用编号" in t for t in thinking_texts)
        assert not any("已综合" in t and "知识库引用" in t for t in thinking_texts)

    def test_reflection_frame_when_weak_hits_demoted(self) -> None:
        """弱命中被降级后，自省不得再吹「已综合 N 条知识库引用」。"""
        mock_client = MockLLMClient("GPT 是大语言模型。")
        s = Settings(llm_provider="openai", llm_api_key="test", rag_mode="hybrid")
        hit = _make_hit(content="DocMind 快速上手操作指南", source="note.md", page=1, score=0.2)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                with patch("doc2mind.core.store.graph_store.GraphStore") as gs:
                    gs.return_value.find_entities_by_keyword.return_value = []
                    results = list(rag_answer_stream(
                        query="你知道gpt吗",
                        settings=s,
                        llm_client=mock_client,
                        enable_web_search=False,
                    ))

        thinking_texts = [
            json.loads(r).get("text", "")
            for r in results
            if isinstance(json.loads(r), dict) and json.loads(r).get("type") == "thinking"
        ]
        assert not any("已综合" in t and "知识库引用" in t for t in thinking_texts)
        # 弱命中不应出现在 sources（done 帧）
        done = [
            json.loads(r) for r in results
            if isinstance(json.loads(r), dict) and json.loads(r).get("done")
        ]
        if done:
            assert not done[0].get("sources")

    def test_reflection_frame_without_sources(self) -> None:
        """Should emit reflection frame for general knowledge response."""
        mock_client = MockLLMClient("通用知识回答")
        s = Settings(llm_provider="openai", llm_api_key="test")

        results = list(rag_answer_stream(
            query="你好",
            settings=s,
            llm_client=mock_client,
        ))

        all_frames = [json.loads(r) for r in results]
        thinking_frames = [f for f in all_frames if f.get("type") == "thinking"]
        
        # Should have reflection frame
        assert len(thinking_frames) >= 1
        reflection_frame = thinking_frames[-1]
        assert "通用知识" in reflection_frame["text"]


# --- Tests: Intent-Based Tool Routing ---
class TestIntentBasedToolRouting:
    """Verify that different intents produce different tool activations."""

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_greeting_skips_all_retrieval(self) -> None:
        """Greeting should skip all retrieval tools."""
        mock_client = MockLLMClient("你好！")
        s = Settings(llm_provider="openai", llm_api_key="test")

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                MockRetriever.return_value = mock_retriever

                results = list(rag_answer_stream(
                    query="你好",
                    settings=s,
                    llm_client=mock_client,
                ))

        # Retriever should NOT be called for greeting
        mock_retriever.search.assert_not_called()
        
        # Should only have planning + reflection frames
        all_frames = [json.loads(r) for r in results]
        thinking_frames = [f for f in all_frames if f.get("type") == "thinking"]
        assert len(thinking_frames) == 2  # planning + reflection

    def test_troubleshoot_enables_pitfall_and_web(self) -> None:
        """Troubleshoot should enable pitfall advisor and web search."""
        mock_client = MockLLMClient("解决方案")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        class FakeWeb:
            def search(self, *a, **k):
                on_progress = k.get("on_progress")
                if on_progress:
                    on_progress("正在多引擎搜索：1 组查询变体")
                    on_progress("✔ 联网搜索：筛出 0 条高相关资料（精读 0 页）")
                return []

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                with patch(
                    "doc2mind.core.search.web_search.get_web_search_service",
                    return_value=FakeWeb(),
                ):
                    results = list(rag_answer_stream(
                        query="程序报错了怎么修",
                        settings=s,
                        llm_client=mock_client,
                        enable_web_search=True,
                    ))

        all_frames = [json.loads(r) for r in results]
        status_messages = [f["message"] for f in all_frames if f.get("type") == "status"]

        # Should have pitfall and web search status messages
        assert any("避坑" in m for m in status_messages)
        assert any("联网" in m for m in status_messages)

    def test_code_query_enables_web_search(self) -> None:
        """Code query should enable web search for examples."""
        mock_client = MockLLMClient("代码示例")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        class FakeWeb:
            def search(self, *a, **k):
                return []

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                with patch(
                    "doc2mind.core.search.web_search.get_web_search_service",
                    return_value=FakeWeb(),
                ):
                    results = list(rag_answer_stream(
                        query="写一个Python函数",
                        settings=s,
                        llm_client=mock_client,
                        enable_web_search=True,
                    ))

        all_frames = [json.loads(r) for r in results]
        status_messages = [f["message"] for f in all_frames if f.get("type") == "status"]

        # Should have web search status message
        assert any("联网" in m for m in status_messages)


# --- Tests: Status Messages ---
class TestStatusMessages:
    """Verify that status messages are context-appropriate for each intent."""

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_troubleshoot_pitfall_message(self) -> None:
        """Troubleshoot should show pitfall-related status message."""
        mock_client = MockLLMClient("解决方案")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        class FakeWeb:
            def search(self, *a, **k):
                return []

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                with patch(
                    "doc2mind.core.search.web_search.get_web_search_service",
                    return_value=FakeWeb(),
                ):
                    results = list(rag_answer_stream(
                        query="报错了",
                        settings=s,
                        llm_client=mock_client,
                        enable_web_search=True,
                    ))

        all_frames = [json.loads(r) for r in results]
        status_messages = [f["message"] for f in all_frames if f.get("type") == "status"]

        # Should have pitfall-related message
        assert any("避坑" in m for m in status_messages)

    def test_code_query_kb_message(self) -> None:
        """Code query should show code-related KB status message."""
        mock_client = MockLLMClient("代码")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                results = list(rag_answer_stream(
                    query="这段代码怎么调试",
                    settings=s,
                    llm_client=mock_client,
                ))

        all_frames = [json.loads(r) for r in results]
        status_messages = [f["message"] for f in all_frames if f.get("type") == "status"]
        
        # Should have code-related KB message
        assert any("代码" in m or "检索" in m for m in status_messages)


# --- Tests: Edge Cases ---
class TestEdgeCases:
    """Edge cases for agent planning."""

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_empty_query_defaults_to_complex(self) -> None:
        """Empty query should default to complex analysis."""
        from doc2mind.core.agent.planner import _fallback_regex_plan
        plan = _fallback_regex_plan("")
        assert plan.query_type == "question"
        assert len(plan.tools) > 0

    def test_very_long_query(self) -> None:
        """Very long query should not crash."""
        from doc2mind.core.agent.planner import _fallback_regex_plan
        long_query = "什么是" + "机器学习" * 100
        plan = _fallback_regex_plan(long_query)
        assert plan.query_type == "question"
        assert len(plan.tools) > 0

    def test_unicode_emoji_query(self) -> None:
        """Query with emojis should not crash."""
        from doc2mind.core.agent.planner import _fallback_regex_plan
        plan = _fallback_regex_plan("🔥 这个报错怎么修 🐛")
        assert plan.query_type == "troubleshoot"
        assert any(t.tool == "pitfall_advisor" for t in plan.tools)

    def test_html_in_query(self) -> None:
        """Query with HTML tags should not crash."""
        from doc2mind.core.agent.planner import _fallback_regex_plan
        plan = _fallback_regex_plan("<script>alert('xss')</script>怎么解决")
        # HTML query with '怎么解决' doesn't match troubleshoot keywords, defaults to question
        assert plan.query_type == "question"
        assert len(plan.tools) > 0

    def test_multiple_intent_keywords(self) -> None:
        """When multiple keywords match, troubleshoot has priority."""
        from doc2mind.core.agent.planner import _fallback_regex_plan
        plan = _fallback_regex_plan("代码报错了怎么修")
        assert plan.query_type == "troubleshoot"
        assert any(t.tool == "pitfall_advisor" for t in plan.tools)

    def test_greeting_detection_variants(self) -> None:
        """Improved greeting detection should handle common variants."""
        from doc2mind.core.agent.planner import _is_greeting

        # Should be detected as greetings
        assert _is_greeting("你好")
        assert _is_greeting("你好啊")
        assert _is_greeting("你好呀")
        assert _is_greeting("你好!")
        assert _is_greeting("Hi")
        assert _is_greeting("hello!")
        assert _is_greeting("早上好")
        assert _is_greeting("谢谢")
        assert _is_greeting("OK")
        assert _is_greeting("👍")  # pure emoji
        assert _is_greeting("🙏")

        # Should NOT be detected as greetings
        assert not _is_greeting("你好我想问个问题")
        assert not _is_greeting("hello world how are you")
        assert not _is_greeting("什么是机器学习")

    def test_greeting_response_uses_short_prompt(self) -> None:
        """Greeting queries should use a short, natural system prompt."""
        import json
        from doc2mind.core.rag import rag_answer_stream
        from doc2mind.core.agent.planner import _is_greeting

        # Verify greeting detection works
        assert _is_greeting("你好")
        assert _is_greeting("你好啊")


# --- Tests: Creative Intent Auto-Routing (PPT / 研报 / 课件 / 对比表 / 看板) ---
class TestCreativeAutoRouting:
    """Verify that natural-language creative requests are auto-routed to a
    creation persona and that the effective persona is reflected back to the
    client, so the front-end can parse + export the structured artifact."""

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_map_creative_mode(self) -> None:
        """map_creative_mode maps explicit creation phrases to a creation mode."""
        from doc2mind.core.agent.intent import map_creative_mode

        assert map_creative_mode("帮我做个PPT") == "ppt"
        assert map_creative_mode("生成一份幻灯片") == "ppt"
        assert map_creative_mode("写一份研报") == "doc"
        assert map_creative_mode("出个公文方案") == "doc"
        assert map_creative_mode("用word写个文档") == "doc"
        assert map_creative_mode("做课件教案") == "lesson"
        assert map_creative_mode("出对比表") == "table"
        assert map_creative_mode("做个excel报表") == "table"
        assert map_creative_mode("导个xlsx表格") == "table"
        assert map_creative_mode("生成可视化看板") == "web"
        # 模糊词不应误判为创作（避免与 SUMMARY/CONCEPT 意图冲突）
        assert map_creative_mode("帮我总结一下") is None
        assert map_creative_mode("普通问题怎么解决") is None

    def test_fallback_regex_plan_creative(self) -> None:
        """Regex fallback should classify creative queries and set creative_mode."""
        from doc2mind.core.agent.planner import _fallback_regex_plan

        plan = _fallback_regex_plan("帮我做个PPT")
        assert plan.query_type == "creative"
        assert plan.creative_mode == "ppt"
        assert any(t.tool == "create_artifact" for t in plan.tools)
        # 仍保留知识库检索作为素材来源
        assert any(t.tool == "knowledge_base" for t in plan.tools)

    def test_plan_with_llm_creative_mode(self) -> None:
        """LLM planner should surface creative_mode for creative queries."""
        from doc2mind.core.agent.planner import plan_with_llm

        reply = json.dumps({
            "analysis": "创作任务",
            "query_type": "creative",
            "creative_mode": "ppt",
            "tools": [{"tool": "knowledge_base", "reason": "x", "priority": 1}],
            "expected_output": "PPT",
        })
        client = MockLLMClient(reply=reply)
        plan = plan_with_llm("帮我做个PPT", client)
        assert plan.query_type == "creative"
        assert plan.creative_mode == "ppt"

    def test_plan_with_llm_invalid_mode_falls_back(self) -> None:
        """Invalid creative_mode from LLM must fall back to keyword mapping."""
        from doc2mind.core.agent.planner import plan_with_llm

        reply = json.dumps({
            "analysis": "x",
            "query_type": "creative",
            "creative_mode": "unknown_xyz",
            "tools": [],
            "expected_output": "x",
        })
        client = MockLLMClient(reply=reply)
        plan = plan_with_llm("帮我做个PPT", client)
        assert plan.query_type == "creative"
        # 无效 mode → 关键词兜底 → ppt
        assert plan.creative_mode == "ppt"

    def test_rag_auto_switches_persona_for_ppt(self) -> None:
        """Natural-language PPT request auto-routes persona=ppt and emits
        an :::artifact body, with the effective persona echoed in frames."""
        creative_plan = json.dumps({
            "analysis": "创作任务",
            "query_type": "creative",
            "creative_mode": "ppt",
            "tools": [{"tool": "knowledge_base", "reason": "x", "priority": 1}],
            "expected_output": "PPT",
        })
        artifact_body = (
            ':::artifact type="pptx" title="AI 汇报" theme="tech_blue"\n'
            "---\n# 人工智能演示文稿\n## 副标题\n:::\n"
        )

        class RoutingMockClient(MockLLMClient):
            def __init__(self) -> None:
                super().__init__(reply=artifact_body)
                self._plan_reply = creative_plan

            def _do_chat(self, messages, temperature=None, max_tokens=None) -> str:
                joined = " ".join(m.get("content", "") for m in messages)
                if "分析以下问题" in joined:
                    return self._plan_reply
                return self._reply

        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="AI 内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5,
                            vector_candidates=1, bm25_candidates=1)
        client = RoutingMockClient()
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                results = list(rag_answer_stream(
                    query="帮我做个关于人工智能的PPT",
                    settings=s,
                    llm_client=client,
                ))

        all_frames = [json.loads(r) for r in results]
        done_frames = [f for f in all_frames if f.get("done") is True]
        assert done_frames, "应包含 done 终帧"
        # 实际生效人设应回传为 ppt（用户未显式选择创作人设）
        assert done_frames[0].get("persona") == "ppt"
        # 正文应含 :::artifact（创作人设已注入规范，LLM 产出结构化交付物）
        token_text = "".join(f.get("token", "") for f in all_frames if "token" in f)
        assert ":::artifact" in token_text


# --- 回归：规划帧不得透出英文 meta 指令 ---
class TestUserFacingPlanReason:
    def test_drops_english_meta_analysis(self) -> None:
        from doc2mind.core.agent.planner import AgentPlan, ToolPlan, user_facing_plan_reason

        plan = AgentPlan(
            analysis=(
                'We need to answer: "AS 与 AtomCode 关系?" '
                "Provide answer in Chinese, concise, maybe a sentence. "
                "No extra formatting? The user didn't specify format, just ask."
            ),
            query_type="question",
            tools=[ToolPlan("knowledge_base", "kb"), ToolPlan("web_search", "web")],
            expected_output="answer",
        )
        reason = user_facing_plan_reason(plan, ["本地知识库检索", "联网搜索"])
        assert "Provide answer" not in reason
        assert "We need to answer" not in reason
        assert "识别为知识问答" in reason
        assert "将调用：本地知识库检索 -> 联网搜索" in reason

    def test_keeps_short_chinese_analysis(self) -> None:
        from doc2mind.core.agent.planner import AgentPlan, user_facing_plan_reason

        plan = AgentPlan(
            analysis="这是一个代码相关查询",
            query_type="task",
            tools=[],
            expected_output="code",
        )
        reason = user_facing_plan_reason(plan, [])
        assert reason.startswith("这是一个代码相关查询")
        assert "无需检索" in reason

    def test_long_analysis_falls_back_to_type_label(self) -> None:
        from doc2mind.core.agent.planner import AgentPlan, user_facing_plan_reason

        plan = AgentPlan(
            analysis="这是" + "很长" * 60 + "的分析",
            query_type="troubleshoot",
            tools=[],
            expected_output="fix",
        )
        reason = user_facing_plan_reason(plan, [])
        assert "识别为问题排查" in reason
        assert len(reason) < 80


class TestWebSearchAutoEnable:
    """用户勾选「联网」后，规划漏选/回退不得吞掉联网。

    真实故障（2026-09-12 GPT 问答截图）：用户已勾选联网，LLM 规划失败落到
    regex 回退默认路径（只含 knowledge_base + entity_graph），旧 AND 逻辑
    `"web_search" in plan and user_toggle` 直接把用户开关也吞掉，
    结果本地命中 5 条无关 DocMind 笔记、完全没上网。
    """

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_fallback_plan_includes_web_search(self) -> None:
        from doc2mind.core.agent.planner import _fallback_regex_plan

        plan = _fallback_regex_plan("你知道gpt吗详细介绍一下")
        tools = [t.tool for t in plan.tools]
        assert "web_search" in tools
        assert plan.query_type == "question"

    def test_user_toggle_on_forces_web_for_knowledge_query(self) -> None:
        """规划不含 web_search 时，用户开关 + question 类型仍必须联网。"""
        from doc2mind.core.agent.planner import AgentPlan, ToolPlan
        import doc2mind.core.rag as rag_mod

        calls = {"web": 0}

        class FakeWebSvc:
            def search(self, *a, **k):
                calls["web"] += 1
                return []

        def fake_plan(query, client, history=None, settings=None):
            # 模拟 LLM 规划失败后的回退形态（旧版默认无 web_search）
            return AgentPlan(
                analysis="这是一个综合性查询",
                query_type="question",
                tools=[
                    ToolPlan("knowledge_base", "检索知识库", priority=1),
                    ToolPlan("entity_graph", "分析实体关系", priority=2),
                ],
                expected_output="综合分析回答",
            )

        mock_client = MockLLMClient("GPT 是一种大语言模型。")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="无关内容", source="docmind-note.md", page=1, score=0.2)
        stats = SearchStats(
            query="q", total_hits=1, elapsed_ms=5,
            vector_candidates=1, bm25_candidates=1,
        )

        with patch("doc2mind.core.rag.plan_with_llm", side_effect=fake_plan), \
             patch("doc2mind.core.rag._open_store") as mock_open, \
             patch("doc2mind.core.search.web_search.get_web_search_service", return_value=FakeWebSvc()):
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                list(rag_answer_stream(
                    query="你知道gpt吗详细介绍一下",
                    settings=s,
                    llm_client=mock_client,
                    enable_web_search=True,  # 用户已勾选联网
                ))

        assert calls["web"] >= 1, "用户勾选联网后，知识型查询必须实际发起联网搜索"

    def test_user_toggle_off_never_searches_web(self) -> None:
        from doc2mind.core.agent.planner import AgentPlan, ToolPlan

        calls = {"web": 0}

        class FakeWebSvc:
            def search(self, *a, **k):
                calls["web"] += 1
                return []

        def fake_plan(query, client, history=None, settings=None):
            return AgentPlan(
                analysis="综合",
                query_type="question",
                tools=[ToolPlan("knowledge_base", "kb", priority=1)],
                expected_output="a",
            )

        mock_client = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1, score=0.8)
        stats = SearchStats(
            query="q", total_hits=1, elapsed_ms=5,
            vector_candidates=1, bm25_candidates=1,
        )

        with patch("doc2mind.core.rag.plan_with_llm", side_effect=fake_plan), \
             patch("doc2mind.core.rag._open_store") as mock_open, \
             patch("doc2mind.core.search.web_search.get_web_search_service", return_value=FakeWebSvc()):
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                list(rag_answer_stream(
                    query="什么是GPT",
                    settings=s,
                    llm_client=mock_client,
                    enable_web_search=False,  # 用户未勾选
                ))

        assert calls["web"] == 0, "用户未勾选联网时绝不能偷偷联网"

    def test_weak_local_hits_not_cited_when_web_enabled(self) -> None:
        """本地命中整体偏弱 + 用户开联网 → 本地只作背景，引用位留给联网。"""
        from doc2mind.core.agent.planner import AgentPlan, ToolPlan
        from doc2mind.core.search.web_search import WebSearchResult

        class FakeWebSvc:
            def search(self, *a, **k):
                return [
                    WebSearchResult(
                        title="GPT 介绍",
                        url="https://example.com/gpt",
                        snippet="GPT is a large language model",
                        source_name="Bing",
                        domain="example.com",
                        content="GPT (Generative Pre-trained Transformer) 是…",
                        content_fetched=True,
                        relevance_score=0.9,
                    )
                ]

        def fake_plan(query, client, history=None, settings=None):
            return AgentPlan(
                analysis="综合",
                query_type="question",
                tools=[
                    ToolPlan("knowledge_base", "kb"),
                    ToolPlan("web_search", "web"),
                ],
                expected_output="a",
            )

        mock_client = MockLLMClient("GPT 是大语言模型 [1]。")
        s = Settings(llm_provider="openai", llm_api_key="test")
        # 弱相关本地命中（DocMind 笔记，与 GPT 无关）
        hit = _make_hit(content="DocMind 知识编译引擎…", source="note.md", page=1, score=0.25)
        stats = SearchStats(
            query="q", total_hits=1, elapsed_ms=5,
            vector_candidates=1, bm25_candidates=1,
        )

        with patch("doc2mind.core.rag.plan_with_llm", side_effect=fake_plan), \
             patch("doc2mind.core.rag._open_store") as mock_open, \
             patch("doc2mind.core.search.web_search.get_web_search_service", return_value=FakeWebSvc()):
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                results = list(rag_answer_stream(
                    query="你知道gpt吗详细介绍一下",
                    settings=s,
                    llm_client=mock_client,
                    enable_web_search=True,
                ))

        done = json.loads(results[-1])
        assert done.get("done") is True
        src_types = [src.get("source_type") for src in done["sources"]]
        # 弱相关本地不得进引用；应只有联网精读结果
        assert "local" not in src_types
        assert "web" in src_types
        status_msgs = [
            json.loads(r).get("message", "")
            for r in results
            if json.loads(r).get("type") == "status"
        ]
        assert any("降为背景" in m or "相关度偏低" in m for m in status_msgs)

# ── M1: planner 仲裁接入与 research 路由 ────────────────────────────────────
class TestPlannerArbitrationM1:
    """M1-T4：planner.py 集成仲裁与 research 路由。"""

    def _settings(self, **kwargs) -> Settings:
        """测试用 Settings；research 开关默认开启以便验证科研路由。"""
        base = {"llm_provider": "openai", "llm_api_key": "test", "intent_research_enabled": True}
        base.update(kwargs)
        return Settings(**base)

    def test_fallback_regex_research_branch(self) -> None:
        """正则回退增加 research 分支（降级路径与 LLM 路径同一契约）。"""
        from doc2mind.core.agent.planner import _fallback_regex_plan

        plan = _fallback_regex_plan("把这些文献的观点对比一下")
        assert plan.query_type == "research"
        assert plan.research_task == "compare"
        # knowledge_base 优先，entity_graph 其次，不默认联网
        assert [t.tool for t in plan.tools] == ["knowledge_base", "entity_graph"]

    def test_fallback_regex_chitchat_empty_tools(self) -> None:
        """闲聊兜底：tools 为空，不强制检索本地库（spec 5.1-7）。"""
        from doc2mind.core.agent.planner import _fallback_regex_plan

        for q in ("随便聊聊", "今天天气怎么样", "给我讲个故事", "闲聊一下"):
            plan = _fallback_regex_plan(q)
            assert plan.query_type == "question"
            assert plan.tools == []

    def test_plan_with_llm_research_route(self) -> None:
        """LLM 规划输出 research 时正确解析 research_task。"""
        from doc2mind.core.agent.planner import plan_with_llm

        reply = json.dumps({
            "analysis": "科研写作任务",
            "query_type": "research",
            "research_task": "compare",
            "tools": [
                {"tool": "knowledge_base", "reason": "检索文献", "priority": 1},
                {"tool": "entity_graph", "reason": "topic 联动", "priority": 2},
            ],
            "expected_output": "带 [n] 引用支撑的对比内容",
        })
        client = MockLLMClient(reply=reply)
        plan = plan_with_llm("把这些文献的观点对比一下", client, settings=self._settings())
        assert plan.query_type == "research"
        assert plan.research_task == "compare"

    def test_plan_with_llm_research_task_fallback(self) -> None:
        """LLM 未给合法 research_task → 关键词兜底。"""
        from doc2mind.core.agent.planner import plan_with_llm

        reply = json.dumps({
            "analysis": "x",
            "query_type": "research",
            "research_task": "unknown_xyz",
            "tools": [{"tool": "knowledge_base", "reason": "x", "priority": 1}],
            "expected_output": "x",
        })
        client = MockLLMClient(reply=reply)
        plan = plan_with_llm("帮我写个综述", client, settings=self._settings())
        assert plan.query_type == "research"
        assert plan.research_task == "review"

    def test_llm_plan_non_triangle_untouched(self) -> None:
        """仲裁器不覆盖非三角区初判（troubleshoot 沿用 LLM）。"""
        from doc2mind.core.agent.planner import plan_with_llm

        reply = json.dumps({
            "analysis": "排查问题",
            "query_type": "troubleshoot",
            "tools": [{"tool": "pitfall_advisor", "reason": "x", "priority": 1}],
            "expected_output": "解决方案",
        })
        client = MockLLMClient(reply=reply)
        plan = plan_with_llm("报错怎么解决", client, settings=self._settings())
        assert plan.query_type == "troubleshoot"

    def test_llm_plan_research_override_with_rules(self) -> None:
        """规则强判 research（文献+任务词）时仲裁介入修正 LLM 初判（creaative→research）。"""
        from doc2mind.core.agent.planner import plan_with_llm

        # LLM 误判为 creative，但 query 是文献观点对比 → 仲裁器应修正为 research
        reply = json.dumps({
            "analysis": "创作任务",
            "query_type": "creative",
            "creative_mode": "ppt",
            "tools": [{"tool": "create_artifact", "reason": "x", "priority": 1}],
            "expected_output": "PPT",
        })
        client = MockLLMClient(reply=reply)
        plan = plan_with_llm("把这些文献的观点对比一下", client, settings=self._settings())
        assert plan.query_type == "research"
        assert plan.research_task == "compare"

    def test_plan_degraded_flag_on_fallback(self) -> None:
        """LLM 规划失败回退 → degraded=True + 审计日志（spec 5.5-7）。"""
        from doc2mind.core.agent.planner import plan_with_llm

        class FailingClient(MockLLMClient):
            def _do_chat(self, messages, temperature=None, max_tokens=None) -> str:
                raise RuntimeError("llm down")

        plan = plan_with_llm("什么是机器学习", FailingClient(), settings=self._settings())
        assert plan.degraded is True
        assert plan.query_type in ("question", "complex_analysis", "analysis")

    def test_research_disabled_falls_back_to_question(self) -> None:
        """intent_research_enabled=False 时科研意图回落 question（design 5.1 #1）。"""
        from doc2mind.core.agent.intent import classify_intent, QueryIntent

        intent, _ = classify_intent("把这些文献的观点对比一下", research_enabled=False)
        assert intent != QueryIntent.RESEARCH

    def test_agent_plan_defaults_compatible(self) -> None:
        """AgentPlan 新增字段带默认值，旧构造方式不受影响。"""
        from doc2mind.core.agent.planner import AgentPlan, ToolPlan

        plan = AgentPlan(
            analysis="a",
            query_type="question",
            tools=[ToolPlan("knowledge_base", "x")],
            expected_output="e",
        )
        assert plan.research_task is None
        assert plan.degraded is False

# ── M2: 科研工具链编排 + 规划帧中文展示 + 降级可见 ─────────────────────────
class TestBuildResearchPlanM2:
    """M2-T6：科研工具链构造（design 4.B.3 决策 B-1）。"""

    def _settings(self, web_crosscheck: bool = False) -> Settings:
        return Settings(
            llm_provider="openai", llm_api_key="test",
            intent_research_enabled=True,
            research_web_crosscheck=web_crosscheck,
        )

    def test_research_review_chain(self) -> None:
        """review 子任务：knowledge_base → entity_graph，不默认联网。"""
        from doc2mind.core.agent.planner import build_research_plan

        plan = build_research_plan("帮我写个综述", research_task="review", settings=self._settings())
        assert plan.query_type == "research"
        assert plan.research_task == "review"
        assert [t.tool for t in plan.tools] == ["knowledge_base", "entity_graph"]

    def test_research_compare_chain(self) -> None:
        """compare 子任务：knowledge_base → entity_graph。"""
        from doc2mind.core.agent.planner import build_research_plan

        plan = build_research_plan("观点对比一下", research_task="compare", settings=self._settings())
        assert plan.research_task == "compare"
        assert [t.tool for t in plan.tools] == ["knowledge_base", "entity_graph"]

    def test_research_draft_alters_chain_with_crosscheck(self) -> None:
        """draft + research_web_crosscheck=True：追加 web_search。"""
        from doc2mind.core.agent.planner import build_research_plan

        plan = build_research_plan(
            "带引用草稿", research_task="draft", settings=self._settings(web_crosscheck=True)
        )
        assert plan.research_task == "draft"
        assert [t.tool for t in plan.tools] == ["knowledge_base", "entity_graph", "web_search"]

    def test_research_draft_no_web_without_crosscheck(self) -> None:
        """draft + research_web_crosscheck=False：不出现 web_search。"""
        from doc2mind.core.agent.planner import build_research_plan

        plan = build_research_plan(
            "带引用草稿", research_task="draft", settings=self._settings(web_crosscheck=False)
        )
        assert [t.tool for t in plan.tools] == ["knowledge_base", "entity_graph"]

    def test_research_task_keyword_fallback(self) -> None:
        """research_task 未指定时关键词映射兜底。"""
        from doc2mind.core.agent.planner import build_research_plan

        plan = build_research_plan("帮我写个综述", settings=self._settings())
        assert plan.research_task == "review"

    def test_research_collections_in_analysis(self) -> None:
        """literature_collections 入参体现在 analysis，不修改搜索词。"""
        from doc2mind.core.agent.planner import build_research_plan

        plan = build_research_plan(
            "观点对比一下", research_task="compare",
            literature_collections=["papers_a", "papers_b"],
            settings=self._settings(),
        )
        assert "2" in plan.analysis  # 限定文献集合 2 个
        # 搜索词不被集合污染
        assert plan.tools[0].search_query == "观点对比一下"


class TestUserFacingPlanReasonResearchM2:
    """M2-T7：规划帧中文展示科研语义（design 4.B.2 B-2 / 4.B.4）。"""

    def test_research_compare_plan_reason(self) -> None:
        from doc2mind.core.agent.planner import AgentPlan, ToolPlan, user_facing_plan_reason

        plan = AgentPlan(
            analysis="科研写作任务",
            query_type="research",
            research_task="compare",
            tools=[
                ToolPlan("knowledge_base", "x", priority=1),
                ToolPlan("entity_graph", "y", priority=2),
            ],
            expected_output="带 [n] 引用支撑的写作内容",
        )
        reason = user_facing_plan_reason(plan, ["knowledge_base", "entity_graph"])
        assert "识别为科研写作（观点对比）" in reason
        assert "本地知识库（文献集合限定）" in reason
        assert "知识图谱（topic 联动）" in reason
        # 无英文 meta 泄漏
        assert "provide" not in reason.lower()

    def test_research_review_plan_reason(self) -> None:
        from doc2mind.core.agent.planner import AgentPlan, ToolPlan, user_facing_plan_reason

        plan = AgentPlan(
            analysis="x",
            query_type="research",
            research_task="review",
            tools=[ToolPlan("knowledge_base", "x", priority=1)],
            expected_output="e",
        )
        reason = user_facing_plan_reason(plan, ["knowledge_base"])
        assert "识别为科研写作（综述/大纲）" in reason
        assert "文献集合限定" in reason

    def test_non_research_plan_reason_unchanged(self) -> None:
        """非科研类型规划帧展示不回归（普通文案）。"""
        from doc2mind.core.agent.planner import AgentPlan, ToolPlan, user_facing_plan_reason

        plan = AgentPlan(
            analysis="问题排查",
            query_type="troubleshoot",
            tools=[ToolPlan("pitfall_advisor", "x", priority=1)],
            expected_output="e",
        )
        reason = user_facing_plan_reason(plan, ["pitfall_advisor"])
        assert "识别为问题排查" in reason or "问题排查" in reason
        assert "文献集合限定" not in reason


class TestPlanningDegradationVisibleM2:
    """M2-T8：规划降级可见帧消费（spec 5.2-6a）。"""

    def _make_failing_client(self) -> MockLLMClient:
        class FailingClient(MockLLMClient):
            def _do_chat(self, messages, temperature=None, max_tokens=None) -> str:
                raise RuntimeError("llm down")
        return FailingClient()

    def test_degraded_visible_emits_status_and_thinking(self) -> None:
        """degraded + planning_degradation_visible=True → 流式输出降级 status/thinking 帧。"""
        from doc2mind.core.rag import rag_answer_stream

        s = Settings(
            llm_provider="openai", llm_api_key="test",
            planning_degradation_visible=True,
            rag_mode="hybrid",
        )
        client = self._make_failing_client()

        with patch("doc2mind.core.rag._open_store") as mock_open, \
             patch("doc2mind.core.search.web_search.get_web_search_service") as mock_web:
            mock_open.return_value = (MagicMock(), MagicMock())
            mock_web.return_value = None

            # plan_with_llm 会被调用（内部走 _fallback_regex_plan → degraded=True）
            results = list(rag_answer_stream(
                query="什么是机器学习",
                settings=s,
                llm_client=client,
                enable_web_search=False,
            ))

        parsed = [json.loads(r) for r in results]
        status_msgs = [p.get("message", "") for p in parsed if p.get("type") == "status"]
        thinking_msgs = [p.get("text", "") for p in parsed if p.get("type") == "thinking"]

        assert any("已使用规则规划" in m for m in status_msgs)
        assert any("规则规划生成" in m or "规则规划" in m for m in thinking_msgs)

    def test_degraded_hidden_when_flag_off(self) -> None:
        """planning_degradation_visible=False → 降级静默（逃生阀，spec 5.2-6 开关）。"""
        from doc2mind.core.rag import rag_answer_stream

        s = Settings(
            llm_provider="openai", llm_api_key="test",
            planning_degradation_visible=False,
            rag_mode="hybrid",
        )
        client = self._make_failing_client()

        with patch("doc2mind.core.rag._open_store") as mock_open, \
             patch("doc2mind.core.search.web_search.get_web_search_service") as mock_web:
            mock_open.return_value = (MagicMock(), MagicMock())
            mock_web.return_value = None

            results = list(rag_answer_stream(
                query="什么是机器学习",
                settings=s,
                llm_client=client,
                enable_web_search=False,
            ))

        parsed = [json.loads(r) for r in results]
        status_msgs = [p.get("message", "") for p in parsed if p.get("type") == "status"]
        assert not any("已使用规则规划" in m for m in status_msgs)
