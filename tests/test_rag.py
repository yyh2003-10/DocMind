"""RAG 编排单元测试（mock LLM + 内存检索，不依赖真实向量库）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from doc2mind.core.config import Settings
from doc2mind.core.llm.base import LLMClient
from doc2mind.core.rag import (
    _CHAT_SESSIONS,
    _MAX_SESSIONS,
    RagAnswer,
    RagError,
    SourceRef,
    _build_context,
    _cap_history,
    _expand_hyde,
    _expand_multi_queries,
    _format_source_ref,
    _load_history,
    _max_history,
    _merge_hits,
    _retrieve_with_expansion,
    _save_history,
    _truncate_history_by_token_budget,
    clear_session,
    rag_answer,
    rag_answer_stream,
)
from doc2mind.core.retriever.search import SearchHit, SearchStats, StoredChunkMeta


# --- Mock LLM Client ---
class MockLLMClient(LLMClient):
    def __init__(self, reply: str = "根据资料，测试回答内容。") -> None:
        self._reply = reply
        self.last_messages: list[dict] = []

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


# --- Tests: _format_source_ref ---
class TestFormatSourceRef:
    def test_basic(self) -> None:
        hit = _make_hit(source="report.pdf", page=5)
        ref = _format_source_ref(1, hit)
        assert "[1]" in ref
        assert "report.pdf" in ref
        assert "p.5" in ref
        assert "测试内容" in ref

    def test_with_heading(self) -> None:
        hit = _make_hit(heading="第一章")
        ref = _format_source_ref(2, hit)
        assert "第一章" in ref

    def test_no_page(self) -> None:
        hit = _make_hit(page=None)
        ref = _format_source_ref(3, hit)
        assert "[3]" in ref
        assert "p." not in ref


# --- Tests: _build_context ---
class TestBuildContext:
    def test_empty_hits(self) -> None:
        context, sources = _build_context([])
        assert context == ""
        assert sources == []

    def test_multiple_hits(self) -> None:
        hits = [_make_hit(score=0.9), _make_hit(content="第二段", score=0.7)]
        context, sources = _build_context(hits)
        assert "[1]" in context
        assert "[2]" in context
        assert len(sources) == 2
        assert sources[0].index == 1
        assert sources[1].score == 0.7

    def test_score_type_prefers_rerank(self) -> None:
        base = _make_hit(score=0.02)
        hit = SearchHit(
            chunk=base.chunk, score=0.02, match_type="hybrid",
            vector_score=0.71, bm25_score=0.0, rank=0, rerank_score=0.87,
        )
        _, sources = _build_context([hit])
        assert sources[0].score == 0.87
        assert sources[0].score_type == "rerank"

    def test_score_type_fallback_vector_bm25_rrf(self) -> None:
        """无重排时：向量相似度 > BM25 匹配度 > RRF 排名分（仅最后回退）。"""
        chunk = _make_hit().chunk
        hits = [
            SearchHit(chunk=chunk, score=0.02, match_type="vector",
                      vector_score=0.71, bm25_score=0.0, rank=0),
            SearchHit(chunk=chunk, score=0.02, match_type="bm25",
                      vector_score=0.0, bm25_score=0.83, rank=1),
            SearchHit(chunk=chunk, score=0.02, match_type="hybrid",
                      vector_score=0.0, bm25_score=0.0, rank=2),
        ]
        _, sources = _build_context(hits)
        assert [(s.score, s.score_type) for s in sources] == [
            (0.71, "vector"), (0.83, "bm25"), (0.02, "rrf"),
        ]
        # RRF 回退时上下文标签应标为「排名分」而非「相似度」
        assert "排名分: 0.02" in _build_context([hits[2]])[0]


# --- Tests: 会话历史管理 ---
class TestSessionHistory:
    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_load_history_new_session(self) -> None:
        cid, history = _load_history(None)
        assert cid.startswith("chat-")
        assert history == []

    def test_load_history_existing_session(self) -> None:
        _CHAT_SESSIONS["test-id"] = [{"role": "user", "content": "hi"}]
        cid, history = _load_history("test-id")
        assert cid == "test-id"
        assert len(history) == 1

    def test_save_history_truncates(self) -> None:
        msgs = [{"role": "user", "content": f"q{i}"} for i in range(25)]
        _save_history("test-session", msgs)
        assert len(_CHAT_SESSIONS["test-session"]) == 20  # _MAX_HISTORY

    def test_max_history_configurable(self) -> None:
        """rag_max_history_messages 覆盖历史上限（条）；0/负数回退默认；下限 2。"""
        assert _max_history(Settings(rag_max_history_messages=5)) == 5
        assert _max_history(Settings(rag_max_history_messages=1)) == 2  # 下限保护
        assert _max_history(Settings(rag_max_history_messages=0)) == 20  # 0 = 内置默认
        assert _max_history(Settings(rag_max_history_messages=-3)) == 20

    def test_save_history_truncates_by_configured_messages(self, monkeypatch) -> None:
        """_save_history 按配置的 rag_max_history_messages 截断，而非硬编码 20。"""
        monkeypatch.setattr(
            "doc2mind.core.rag.get_settings",
            lambda: Settings(rag_max_history_messages=5),
        )
        msgs = [{"role": "user", "content": f"q{i}"} for i in range(25)]
        _save_history("test-session-turns", msgs)
        assert len(_CHAT_SESSIONS["test-session-turns"]) == 5

    def test_clear_session(self) -> None:
        _CHAT_SESSIONS["to-clear"] = [{"role": "user", "content": "x"}]
        result = clear_session("to-clear")
        assert result is True
        assert "to-clear" not in _CHAT_SESSIONS

    def test_clear_nonexistent_session(self) -> None:
        result = clear_session("nonexistent")
        assert result is False

    def test_session_lru_evicts_oldest(self) -> None:
        """超过 _MAX_SESSIONS 时淘汰最久未创建的会话。"""
        for i in range(_MAX_SESSIONS + 10):
            _load_history(f"session-{i}")

        assert len(_CHAT_SESSIONS) == _MAX_SESSIONS
        # 最先创建的最先被淘汰
        assert "session-0" not in _CHAT_SESSIONS
        assert "session-9" not in _CHAT_SESSIONS
        # 最后创建的还在
        assert f"session-{_MAX_SESSIONS + 9}" in _CHAT_SESSIONS

    def test_session_lru_touch_keeps_recent(self) -> None:
        """访问过的会话移到队尾（最近使用优先保留）。"""
        _load_history("old")                       # 最早创建
        for i in range(_MAX_SESSIONS - 1):
            _load_history(f"fill-{i}")             # 塞满到上限
        _load_history("old")                       # 访问 → 移到队尾
        _load_history("overflow")                  # 再塞一个 → 淘汰队首

        assert len(_CHAT_SESSIONS) == _MAX_SESSIONS
        assert "old" in _CHAT_SESSIONS             # 刚访问过，保留
        assert "fill-0" not in _CHAT_SESSIONS      # 队首最旧，被淘汰


# --- Tests: rag_answer ---
class TestHistoryTrim:
    """历史截断语义（AUD-012：占位提示不能被二次切掉）。"""

    def _long_history(self, n: int = 30) -> list[dict]:
        return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"问题{item}的一些详细内容描述"}
                for i, item in enumerate(range(n))]

    def test_placeholder_inserted_when_token_budget_drops(self) -> None:
        history = self._long_history()
        truncated = _truncate_history_by_token_budget(
            history, max_tokens=60, chars_per_token=2.5
        )
        assert any(
            m.get("role") == "system" and str(m.get("content", "")).startswith("…(已省略")
            for m in truncated
        )

    def test_placeholder_survives_cap_history(self) -> None:
        """修复前：token 裁剪插入的占位提示会被后续 `[-N:]` 切片二次切掉。"""
        history = self._long_history()
        truncated = _truncate_history_by_token_budget(
            history, max_tokens=60, chars_per_token=2.5
        )
        capped = _cap_history(truncated, n=6)
        assert any(
            m.get("role") == "system" and str(m.get("content", "")).startswith("…(已省略")
            for m in capped
        ), f"占位提示被 _cap_history 切掉: {capped[:2]}"

    def test_cap_history_keeps_most_recent_messages(self) -> None:
        history = self._long_history(n=10)
        capped = _cap_history(history, n=4)
        assert len(capped) == 4
        assert [m["content"] for m in capped] == [f"问题{6 + i}的一些详细内容描述" for i in range(4)]

    def test_cap_history_noop_when_within_limit(self) -> None:
        history = self._long_history(n=3)
        assert _cap_history(history, n=10) is history


class TestRagAnswer:
    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_no_llm_configured_raises(self) -> None:
        s = Settings(llm_provider="none")
        with pytest.raises(RagError, match="未配置 LLM"):
            rag_answer(query="test", settings=s, llm_client=None)

    def test_empty_retrieval_returns_hint(self) -> None:
        """无检索命中时返回知识库为空的提示，不调 LLM。"""
        mock_client = MockLLMClient("不应被调用")
        s = Settings(llm_provider="openai", llm_api_key="test")

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([], SearchStats(query="test", total_hits=0, elapsed_ms=5, vector_candidates=0, bm25_candidates=0))
                MockRetriever.return_value = mock_retriever

                result = rag_answer(
                    query="test question",
                    settings=s,
                    llm_client=mock_client,
                )

            assert result.answer == "知识库中未找到与问题相关的内容，我无法回答。请先摄入相关文档再提问。"
            assert result.total_chunks == 0
            assert result.chat_id is not None

    def test_hybrid_mode_empty_retrieval_calls_llm(self) -> None:
        """混合增强模式：知识库为空时，回退调用 LLM 进行通用知识回答。"""
        mock_client = MockLLMClient("这是基于通用知识的解答。")
        s = Settings(llm_provider="openai", llm_api_key="test", rag_mode="hybrid")

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([], SearchStats(query="test", total_hits=0, elapsed_ms=5, vector_candidates=0, bm25_candidates=0))
                MockRetriever.return_value = mock_retriever

                result = rag_answer(
                    query="test question",
                    settings=s,
                    llm_client=mock_client,
                )

            assert result.answer == "这是基于通用知识的解答。"
            assert result.total_chunks == 0
            assert result.chat_id is not None

    def test_full_rag_flow(self) -> None:
        """完整 RAG 流程：检索 → 上下文 → LLM → 带来源回答。"""
        mock_client = MockLLMClient("根据资料，DocMind 采用分层架构。")
        s = Settings(llm_provider="openai", llm_api_key="test")

        hit = _make_hit(content="DocMind 采用分层架构...", source="doc.pdf", page=3, heading="架构概述")
        stats = SearchStats(query="架构", total_hits=1, elapsed_ms=10, vector_candidates=5, bm25_candidates=5)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                result = rag_answer(
                    query="架构是什么？",
                    settings=s,
                    llm_client=mock_client,
                )

            assert result.answer == "根据资料，DocMind 采用分层架构。"
            assert result.total_chunks == 1
            assert result.sources[0].source == "doc.pdf"
            assert result.sources[0].page == 3
            assert result.sources[0].heading == "架构概述"
            assert result.model == "mock-model"
            assert result.provider == "mock"

    def test_empty_reply_raises_not_persisted(self) -> None:
        """AUD-015：与流式路径一致——模型返回空/空白回答时抛 RagError，
        而不是把空 assistant 消息落库污染历史。"""
        mock_client = MockLLMClient("   \n  ")  # 纯空白
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                with pytest.raises(RagError, match="空内容"):
                    rag_answer(query="问题", settings=s, llm_client=mock_client)

        # 回答为空时不得写入任何消息轮（_load_history 只会留下空会话占位）
        assert all(len(m) == 0 for m in _CHAT_SESSIONS.values())

    def test_context_contains_source_labels(self) -> None:
        """验证传给 LLM 的消息包含 [1] [2] 来源标注。"""
        mock_client = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test")

        hit = _make_hit(content="知识内容", source="ref.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                rag_answer(query="问题", settings=s, llm_client=mock_client)

            # 检查 LLM 收到的最后一条 user 消息包含来源标注
            user_msg = mock_client.last_messages[-1]["content"]
            assert "[1]" in user_msg
            assert "ref.pdf" in user_msg
            assert "知识内容" in user_msg

    def test_multi_turn_uses_history(self) -> None:
        """多轮对话应携带历史消息。"""
        mock_client = MockLLMClient("好的回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                # 第一轮
                r1 = rag_answer(query="什么是架构？", settings=s, llm_client=mock_client)
                cid = r1.chat_id

                # 第二轮（传入 chat_id）
                rag_answer(query="那组件呢？", chat_id=cid, settings=s, llm_client=mock_client)

            # 第二轮的消息应包含历史
            messages = mock_client.last_messages
            # 消息格式: [system, history_user, history_assistant, user_with_context]
            assert len(messages) >= 4
            # 第二条 user 消息是第一轮的历史
            assert any(m["content"] == "什么是架构？" for m in messages if m["role"] == "user")

    def test_collections_passed_to_retriever(self) -> None:
        """多选集合列表应透传给 retriever.search。"""
        mock_client = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                rag_answer(
                    query="问题",
                    settings=s,
                    llm_client=mock_client,
                    collections=["docs-a", "docs-b"],
                )

                args, kwargs = mock_retriever.search.call_args
                assert kwargs["collection"] == ["docs-a", "docs-b"]
                assert kwargs["min_score"] == 0.0

    def test_collections_take_precedence_over_collection(self) -> None:
        """collections 列表优先于单集合 collection 参数。"""
        mock_client = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                rag_answer(
                    query="问题",
                    collection="default",
                    settings=s,
                    llm_client=mock_client,
                    collections=["docs-a"],
                )

                args, kwargs = mock_retriever.search.call_args
                assert kwargs["collection"] == ["docs-a"]

    def test_min_score_filters_low_component_scores(self) -> None:
        """rag_min_score 按 max(向量分, BM25 分) 过滤噪声命中。"""
        mock_client = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test", rag_min_score=0.7)
        # vector=0.9/bm25=0.81 → 保留；vector=0.3/bm25=0.27 → 过滤
        good = _make_hit(content="相关内容", score=0.9)
        bad = _make_hit(content="无关噪声", score=0.3)
        stats = SearchStats(query="q", total_hits=2, elapsed_ms=5, vector_candidates=2, bm25_candidates=2)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([good, bad], stats)
                MockRetriever.return_value = mock_retriever

                result = rag_answer(query="问题", settings=s, llm_client=mock_client)

            # 只有 good 进入上下文
            assert len(result.sources) == 1
            assert result.sources[0].source == "test.pdf"
            user_msg = mock_client.last_messages[-1]["content"]
            assert "相关内容" in user_msg
            assert "无关噪声" not in user_msg

    def test_min_score_zero_keeps_all(self) -> None:
        """默认 rag_min_score=0.0 不在检索阶段过滤命中（回归保护）。

        注意：引用层另有过滤——相关度 <0.30 或主题不匹配的弱命中不再进
        SourceRef（底层能力改造，避免「什么是GPT」挂 5 条无关 DocMind 笔记）。
        本用例用与 query 主题匹配且分数中等的命中，验证 min_score=0 本身不过滤。
        """
        mock_client = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test")  # rag_min_score 默认 0.0
        hit = _make_hit(content="这是一个关于问题的说明文档。", score=0.55)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                result = rag_answer(query="问题", settings=s, llm_client=mock_client)

            assert len(result.sources) == 1  # 主题匹配且分数中等：仍被保留

    def test_very_weak_hit_not_cited_by_default(self) -> None:
        """相关度 <0.30 的命中即使 rag_min_score=0 也不进引用列表。"""
        mock_client = MockLLMClient("基于通用知识回答。")
        s = Settings(llm_provider="openai", llm_api_key="test", rag_mode="hybrid")
        low = _make_hit(content="低分噪声", score=0.1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([low], stats)
                MockRetriever.return_value = mock_retriever
                with patch("doc2mind.core.store.graph_store.GraphStore") as gs:
                    gs.return_value.find_entities_by_keyword.return_value = []
                    result = rag_answer(
                        query="问题", settings=s, llm_client=mock_client,
                        enable_web_search=False,
                    )
        assert result.sources == []

    def test_rag_answer_dataclass(self) -> None:
        """验证 RagAnswer 和 SourceRef dataclass。"""
        answer = RagAnswer(
            answer="test",
            sources=[SourceRef(index=1, source="a.pdf", format="pdf", page=1, score=0.9)],
            chat_id="chat-123",
            elapsed_ms=100,
            total_chunks=1,
            model="gpt",
            provider="openai",
        )
        assert answer.answer == "test"
        assert answer.sources[0].source == "a.pdf"
        assert answer.elapsed_ms == 100


# --- Tests: rag_answer_stream ---
class TestRagAnswerStream:
    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_stream_returns_tokens(self) -> None:
        """流式返回逐 token JSON 行，终帧含元数据。"""
        mock_client = MockLLMClient("流式回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="文档内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                import json

                from doc2mind.core.rag import rag_answer_stream
                results = list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                ))

        # 检查 token 行（跳过 status 帧）
        assert len(results) >= 2  # 至少 token + done
        token_frames = [json.loads(r) for r in results[:-1] if "token" in json.loads(r)]
        assert len(token_frames) >= 1
        assert token_frames[0]["token"] == "流式回答"

        # 检查终帧
        done_data = json.loads(results[-1])
        assert done_data.get("done") is True
        assert done_data["chat_id"] is not None
        assert done_data["model"] == "mock-model"
        assert done_data["total_chunks"] == 1
        # 终帧来源必须携带完整 snippet（正文切片预览依赖）与 chunk_id
        assert done_data["sources"][0]["snippet"] == "文档内容"
        assert done_data["sources"][0]["source"] == "doc.pdf"
        assert done_data["sources"][0]["page"] == 1

    def test_stream_yields_status_events(self) -> None:
        """流式路径：检索阶段产出 status 帧，token 帧在 status 之后。"""
        import json
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

                from doc2mind.core.rag import rag_answer_stream
                results = list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                ))

        all_frames = [json.loads(r) for r in results]
        status_frames = [f for f in all_frames if f.get("type") == "status"]
        # 至少有检索+避坑 两条 status 帧
        assert len(status_frames) >= 2
        status_messages = [f["message"] for f in status_frames]
        assert any("检索知识库" in m for m in status_messages)
        # 检索阶段 status 在首 token 前；完成态 status 允许在 token 后（替换「正在生成」）
        first_token_idx = next(i for i, f in enumerate(all_frames) if "token" in f)
        for sf in status_frames:
            if "回答生成完成" in sf.get("message", ""):
                assert all_frames.index(sf) > first_token_idx
            else:
                assert all_frames.index(sf) < first_token_idx

    def test_stream_strict_no_context_done_frame_carries_model_spec(self) -> None:
        """AUD-014：strict 模式无命中早返回的 done 帧也带 model_spec
        （修复前缺字段，客户端回退为原始模型 ID 显示）。"""
        import json
        mock_client = MockLLMClient("不应被调用")
        s = Settings(llm_provider="openai", llm_api_key="test", rag_mode="strict")
        stats = SearchStats(query="q", total_hits=0, elapsed_ms=5, vector_candidates=0, bm25_candidates=0)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([], stats)
                MockRetriever.return_value = mock_retriever

                results = list(rag_answer_stream(
                    query="知识库里有这个答案吗", settings=s, llm_client=mock_client,
                ))

        done = next(f for f in (json.loads(r) for r in results) if f.get("done") is True)
        assert done["model_spec"]["display_name"] == "mock-model"
        assert done["partial"] is False
        assert done["sources"] == []

    def test_stream_stopped_done_frame_carries_model_spec(self) -> None:
        """AUD-014：用户停止生成的 partial done 帧同样带 model_spec。"""
        import json
        import threading
        mock_client = MockLLMClient("不应被调用")
        s = Settings(llm_provider="openai", llm_api_key="test")
        stop_event = threading.Event()
        stop_event.set()  # 进入上下文消费循环前即触发停止
        stats = SearchStats(query="q", total_hits=0, elapsed_ms=5, vector_candidates=0, bm25_candidates=0)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([], stats)
                MockRetriever.return_value = mock_retriever

                results = list(rag_answer_stream(
                    query="普通问题", settings=s, llm_client=mock_client, stop_event=stop_event,
                ))

        done = next(f for f in (json.loads(r) for r in results) if f.get("done") is True)
        assert done["partial"] is True
        assert done["model_spec"]["display_name"] == "mock-model"

    def test_stream_status_steps_carry_details(self) -> None:
        """每个检索阶段完成后发「✔ 详情」状态：命中数/经验数/关联数。"""
        import json
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

                from doc2mind.core.rag import rag_answer_stream
                results = list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                ))

        status_messages = [json.loads(r)["message"] for r in results if "message" in json.loads(r)]
        # 每个「正在…」步骤后紧跟带详情的「✔ …」状态
        # Note: status message varies by intent (e.g. "正在检索知识库..." vs "正在深度检索知识库...")
        assert any("检索知识库" in m for m in status_messages)
        assert any(m.startswith("✔ 检索知识库") and "命中 1 个分块" in m for m in status_messages)
        assert any(m.startswith("✔ 避坑指南") for m in status_messages)
        # 详情帧紧随对应的「正在…」帧之后
        kb_idx = next(i for i, m in enumerate(status_messages) if "检索知识库" in m)
        assert status_messages[kb_idx + 1].startswith("✔ 检索知识库")

    def test_stream_empty_retrieval_returns_hint(self) -> None:
        """空检索：产出提示 token + done 帧（total_chunks=0），不调 LLM。"""
        import json

        mock_client = MockLLMClient("不应被调用")
        s = Settings(llm_provider="openai", llm_api_key="test")

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = (
                    [], SearchStats(query="q", total_hits=0, elapsed_ms=5,
                                    vector_candidates=0, bm25_candidates=0),
                )
                MockRetriever.return_value = mock_retriever

                from doc2mind.core.rag import rag_answer_stream
                results = list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                ))

        assert len(results) >= 2  # 至少提示 token + done（可能有 status 帧）
        # 找到 token 帧（跳过 status 帧）
        token_frames = [json.loads(r) for r in results if "token" in json.loads(r)]
        assert len(token_frames) == 1
        assert "未找到" in token_frames[0]["token"]
        done_data = json.loads(results[-1])
        assert done_data["done"] is True
        assert done_data["total_chunks"] == 0
        assert done_data["sources"] == []

    def test_stream_collections_passed_to_retriever(self) -> None:
        """流式路径：多选集合列表透传给 retriever.search。"""
        mock_client = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                from doc2mind.core.rag import rag_answer_stream
                list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                    collections=["kb-a", "kb-b"],
                ))

                args, kwargs = mock_retriever.search.call_args
                assert kwargs["collection"] == ["kb-a", "kb-b"]

    def test_stream_no_llm_configured_raises(self) -> None:
        """流式路径：LLM 未配置时抛 RagError。"""
        from doc2mind.core.rag import rag_answer_stream

        s = Settings(llm_provider="none")
        with pytest.raises(RagError, match="未配置 LLM"):
            list(rag_answer_stream(query="test", settings=s, llm_client=None))

    def test_stream_llm_error_propagates_cause(self) -> None:
        """流式路径：LLM 流式异常且零产出时，RagError 需携带底层原因（可排障）。"""
        from doc2mind.core.llm.base import LLMError
        from doc2mind.core.rag import rag_answer_stream

        class ErrorMockClient(MockLLMClient):
            def _do_stream_chat(self, messages, temperature=None, max_tokens=None):
                raise LLMError(
                    "OpenAI API 流式调用失败（模型已下架或当前账号无权调用）: "
                    "Error code: 404 - Function not found for account"
                )
                yield  # pragma: no cover — 使其成为生成器

        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                with pytest.raises(RagError) as exc_info:
                    list(rag_answer_stream(query="问题", settings=s, llm_client=ErrorMockClient()))

        # 错误消息带底层原因摘要，而不是只有通用文案
        assert "404" in str(exc_info.value)
        assert "模型已下架或当前账号无权调用" in str(exc_info.value)

    def test_stream_empty_reply_raises(self) -> None:
        """流式路径：模型全程零正文（空流/推理块被过滤）时抛 RagError，不静默空回答。"""
        from doc2mind.core.rag import rag_answer_stream

        class EmptyMockClient(MockLLMClient):
            def _do_stream_chat(self, messages, temperature=None, max_tokens=None):
                return iter(())
                yield  # pragma: no cover — 使其成为生成器

        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                with pytest.raises(RagError, match="空内容"):
                    list(rag_answer_stream(query="问题", settings=s, llm_client=EmptyMockClient()))

    def test_stream_yields_token_per_chunk(self) -> None:
        """子类逐 token 流式实现：每个 chunk 一帧，按序拼接还原完整回答。"""
        import json

        class ChunkedMockClient(MockLLMClient):
            def _do_stream_chat(self, messages, temperature=None, max_tokens=None):
                yield from ["根据", "资料", "回答。"]

        mock_client = ChunkedMockClient()
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                from doc2mind.core.rag import rag_answer_stream
                results = list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                ))

        token_frames = [json.loads(r) for r in results[:-1] if "token" in json.loads(r)]
        assert "".join(f["token"] for f in token_frames) == "根据资料回答。"
        # 历史已保存完整拼接的回答
        cid = json.loads(results[-1])["chat_id"]
        from doc2mind.core.rag import _CHAT_SESSIONS as sessions
        assert sessions[cid][-1] == {"role": "assistant", "content": "根据资料回答。"}


class TestDegenerateAnswerGuard:
    """AnswerGuard 与 RAG 编排的集成：工具调用 JSON / 重复退化必须被拦。"""

    _DUMP = (
        '{\n  "query": "夹爪 动平衡",\n  "region": "cn-zh",\n'
        '  "max_results": 10,\n  "pages": 1\n}\n'
    ) * 20

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def _stream_with_mock(self, reply_chunks: list[str]):
        class DumpMockClient(MockLLMClient):
            def _do_stream_chat(self, messages, temperature=None, max_tokens=None):
                yield from reply_chunks

        mock_client = DumpMockClient()
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(
            query="q", total_hits=1, elapsed_ms=5,
            vector_candidates=1, bm25_candidates=1,
        )
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                results = list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                ))
        return results

    def test_stream_intercepts_tool_call_dump(self) -> None:
        """流式：工具调用 JSON dump 不得作为 token 外泄，终帧带 warning。"""
        import json

        chunks = [self._DUMP[i : i + 80] for i in range(0, len(self._DUMP), 80)]
        results = self._stream_with_mock(chunks)
        frames = [json.loads(r) for r in results]

        token_text = "".join(f.get("token", "") for f in frames if "token" in f)
        assert '"max_results"' not in token_text
        assert token_text.strip() == ""

        done = frames[-1]
        assert done.get("done") is True
        assert done.get("partial") is True
        assert done.get("warning")
        assert "工具调用" in done["warning"] or "退化" in done["warning"]

        # 垃圾不得写入会话历史
        cid = done["chat_id"]
        history = _CHAT_SESSIONS.get(cid, [])
        assistant_turns = [m for m in history if m.get("role") == "assistant"]
        assert assistant_turns == []

    def test_stream_allows_normal_answer(self) -> None:
        """流式：正常回答仍完整通过。"""
        import json

        results = self._stream_with_mock(["夹爪平衡参照 ISO 1940，常用 G6.3。"])
        frames = [json.loads(r) for r in results]
        token_text = "".join(f.get("token", "") for f in frames if "token" in f)
        assert "ISO 1940" in token_text
        assert frames[-1].get("done") is True
        assert not frames[-1].get("warning")

    def test_nonstream_raises_on_tool_call_dump(self) -> None:
        """非流式：dump 回答直接 RagError，不落库。"""
        mock_client = MockLLMClient(self._DUMP)
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="内容", source="doc.pdf", page=1)
        stats = SearchStats(
            query="q", total_hits=1, elapsed_ms=5,
            vector_candidates=1, bm25_candidates=1,
        )
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                with pytest.raises(RagError, match="退化|工具调用"):
                    rag_answer(query="问题", settings=s, llm_client=mock_client)


class TestAutoRetry:
    """上下文溢出自动重试机制。"""

    def test_context_overflow_triggers_retry(self) -> None:
        """模拟上下文溢出错误时，自动重试并最终成功。"""
        import json

        from doc2mind.core.rag import rag_answer_stream

        call_count = 0

        class RetryMockClient(MockLLMClient):
            def _do_stream_chat(self, messages, temperature=None, max_tokens=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # 第一次调用抛出连接中断错误（模拟上下文溢出）
                    raise ConnectionError("Connection reset by peer")
                # 第二次调用成功
                yield from ["重试成功", "回答。"]

        mock_client = RetryMockClient()
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                results = list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                ))

        # 验证重试成功
        token_frames = [json.loads(r) for r in results if "token" in json.loads(r)]
        assert "".join(f["token"] for f in token_frames) == "重试成功回答。"
        # 应该有重试状态消息
        status_frames = [json.loads(r) for r in results if json.loads(r).get("type") == "status"]
        retry_msgs = [f["message"] for f in status_frames if "精简" in f.get("message", "")]
        assert len(retry_msgs) >= 1  # 至少有一条重试状态消息

    def test_non_overflow_error_no_retry(self) -> None:
        """非上下文溢出错误（如 404）不应触发重试。"""
        from doc2mind.core.rag import rag_answer_stream

        call_count = 0

        class NoRetryMockClient(MockLLMClient):
            def _do_stream_chat(self, messages, temperature=None, max_tokens=None):
                nonlocal call_count
                call_count += 1
                raise Exception("Error code: 404 - Function not found")

        mock_client = NoRetryMockClient()
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever

                with pytest.raises(RagError):
                    list(rag_answer_stream(
                        query="问题", settings=s, llm_client=mock_client,
                    ))

        # 404 错误不应触发重试，只调用一次
        assert call_count == 1

    def test_truncate_messages_for_retry(self) -> None:
        """验证 _truncate_messages_for_retry 正确裁剪上下文。"""
        from doc2mind.core.rag import _truncate_messages_for_retry

        # 使用足够长的上下文以便截断生效
        long_context = "这是很长的上下文内容。" * 50  # ~500 字符
        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": f"{long_context}\n\n请基于以上背景与资料回答：你好"},
        ]
        original_len = len(messages[-1]["content"])

        # 第一次重试：裁剪到 50%
        result = _truncate_messages_for_retry(messages, attempt=1)
        user_content = result[-1]["content"]
        assert "你好" in user_content  # query 保留
        assert len(user_content) < original_len  # 内容减少

        # 第二次重试：裁剪到 25%
        result2 = _truncate_messages_for_retry(messages, attempt=2)
        assert len(result2[-1]["content"]) < len(result[-1]["content"])
        assert "你好" in result2[-1]["content"]  # query 仍保留


class TestHistoryTokenBudget:
    """_truncate_history_by_token_budget 的 token 预算截断逻辑。"""

    def test_budget_zero_keeps_all(self) -> None:
        from doc2mind.core.rag import _truncate_history_by_token_budget
        history = [{"role": "user", "content": f"q{i}"} for i in range(5)]
        result = _truncate_history_by_token_budget(history, max_tokens=0)
        assert result == history

    def test_large_budget_keeps_all(self) -> None:
        from doc2mind.core.rag import _truncate_history_by_token_budget
        history = [{"role": "user", "content": "short"} for _ in range(5)]
        result = _truncate_history_by_token_budget(history, max_tokens=10000)
        assert len(result) == 5
        # 无省略 → 无占位消息
        assert not any(m["role"] == "system" and "省略" in m["content"] for m in result)

    def test_small_budget_truncates_oldest(self) -> None:
        from doc2mind.core.rag import _truncate_history_by_token_budget
        # 每条 100 字符 / 2.5 = 40 token,预算 80 → 保留最近 2 条
        history = [{"role": "user", "content": "x" * 100} for _ in range(5)]
        result = _truncate_history_by_token_budget(history, max_tokens=80)
        assert len(result) == 3  # 保留 2 条真实消息 + 1 条占位
        assert result[0]["role"] == "system"
        assert "省略" in result[0]["content"] and "3" in result[0]["content"]
        assert result[1:] == history[-2:]

    def test_budget_reaches_edge_keeps_last_one(self) -> None:
        from doc2mind.core.rag import _truncate_history_by_token_budget
        # 单条历史即使超预算也保留（不能全空）
        history = [{"role": "user", "content": "x" * 500}]
        result = _truncate_history_by_token_budget(history, max_tokens=10)
        assert len(result) == 1

    def test_empty_history_returns_empty(self) -> None:
        from doc2mind.core.rag import _truncate_history_by_token_budget
        assert _truncate_history_by_token_budget([], max_tokens=100) == []


# --- 停止/取消语义：截断回答不得污染会话历史 ---
class StoppableStreamLLM(LLMClient):
    """流式输出中途置位 stop_event 的测试用 LLM。"""

    def __init__(self, stop_event) -> None:
        self._stop = stop_event

    @property
    def model_name(self) -> str:
        return "stoppable-model"

    @property
    def provider(self) -> str:
        return "dummy"

    def _do_chat(self, messages, temperature=None, max_tokens=None) -> str:
        return "完整回答"

    def _do_stream_chat_tagged(self, messages, temperature=None, max_tokens=None, stop_event=None):
        yield ("content", "被截断的部分回答")
        self._stop.set()
        yield ("content", "停止后不应出现的后续内容")


def _stream_frames(llm, stop_event, settings):
    from doc2mind.core.rag import rag_answer_stream

    return list(rag_answer_stream(
        query="测试问题",
        settings=settings,
        llm_client=llm,
        store=MagicMock(),
        embedder=MagicMock(),
        stop_event=stop_event,
        rag_mode="hybrid",
    ))


class TestStreamStopSemantics:
    def test_midstream_stop_truncated_reply_not_persisted(self, tmp_path) -> None:
        """用户中途停止：截断回答不写入历史（内存与 SQLite 均无），done 帧 partial=true。"""
        import json
        import threading

        from doc2mind.core.store.chat_store import ChatStore

        stop = threading.Event()
        llm = StoppableStreamLLM(stop)
        s = Settings(db_path=tmp_path / "t.db")
        _CHAT_SESSIONS.clear()

        frames = _stream_frames(llm, stop, s)
        payloads = [json.loads(f) for f in frames]
        done = next(p for p in payloads if p.get("done"))

        assert done["partial"] is True
        assert "停止" in done["warning"]
        # stop_event 置位后的内容绝不出现（base 泵在停止后会丢弃后续帧，
        # 停止前最后一帧是否送达存在竞态，故不断言其一定出现）
        tokens = "".join(p["token"] for p in payloads if "token" in p)
        assert "停止后不应出现的后续内容" not in tokens

        # 本轮完全不落历史：内存 LRU 为空、SQLite 无消息
        assert _CHAT_SESSIONS.get(done["chat_id"], []) == []
        assert ChatStore(s.db_path).get_history(done["chat_id"], 10) == []

    def test_stop_before_retrieval_returns_partial_done(self, tmp_path) -> None:
        """检索阶段前已停止：立即产出 partial 终帧，不调用 LLM、不落历史。"""
        import json
        import threading

        from doc2mind.core.store.chat_store import ChatStore

        stop = threading.Event()
        stop.set()
        llm = StoppableStreamLLM(stop)
        s = Settings(db_path=tmp_path / "t.db")
        _CHAT_SESSIONS.clear()

        frames = _stream_frames(llm, stop, s)
        payloads = [json.loads(f) for f in frames]
        done = next(p for p in payloads if p.get("done"))

        assert done["partial"] is True
        assert done["sources"] == []
        assert all("token" not in p for p in payloads)  # 未产生任何正文
        assert _CHAT_SESSIONS.get(done["chat_id"], []) == []
        assert ChatStore(s.db_path).get_history(done["chat_id"], 10) == []

    def test_normal_completion_still_persisted(self, tmp_path) -> None:
        """正常完成（stop_event 从未置位）：回答照常入库（回归保护）。"""
        import json
        import threading

        from doc2mind.core.store.chat_store import ChatStore

        stop = threading.Event()  # 保持未置位
        llm = MockLLMClient("正常完成的完整回答")
        s = Settings(db_path=tmp_path / "t.db")
        _CHAT_SESSIONS.clear()

        frames = _stream_frames(llm, stop, s)
        done = next(json.loads(f) for f in frames if json.loads(f).get("done"))

        assert done["partial"] is False
        assert "warning" not in done
        assert ChatStore(s.db_path).get_history(done["chat_id"], 10) != []


# --- C1 查询扩展（可选，LLM 驱动）---
def _hit(id_, content="内容", vec=0.8, bm=0.7, rerank=None):
    chunk = StoredChunkMeta(
        id=id_, content=content, source=f"s{id_}.md", format="md",
        doc_type=None, page=None, heading=None, tokens=50,
        chunk_index=0, collection="t",
    )
    return SearchHit(
        chunk=chunk, score=max(vec, bm), match_type="hybrid",
        vector_score=vec, bm25_score=bm, rank=0, rerank_score=rerank,
    )


class TestC1QueryExpansion:
    def test_merge_hits_dedups_and_ranks(self) -> None:
        """_merge_hits 按 chunk.id 去重，保留相关度最优者，并按相关度降序重排。"""
        a = _hit(1, vec=0.9, bm=0.5)
        a_dup = _hit(1, vec=0.4, bm=0.3)  # 同 id，更差 → 应被丢弃
        b = _hit(2, vec=0.6, bm=0.7)
        c = _hit(3, vec=0.2, bm=0.2)
        merged = _merge_hits([a, a_dup], [b], [c])
        ids = [h.chunk.id for h in merged]
        assert ids == [1, 2, 3]
        assert merged[0].chunk.id == 1 and merged[0].vector_score == 0.9
        assert [h.rank for h in merged] == [0, 1, 2]

    def test_merge_prefers_rerank_score(self) -> None:
        """rerank_score 存在时作为相关度代理参与排序。"""
        no_rerank = _hit(5, vec=0.95, bm=0.9)  # 高分量纲但无重排
        has_rerank = _hit(6, vec=0.5, bm=0.5, rerank=0.99)  # 重排分高
        merged = _merge_hits([no_rerank, has_rerank])
        # 有重排分者优先（0.99 > 0.95）
        assert merged[0].chunk.id == 6
        assert merged[1].chunk.id == 5

    def test_expand_multi_queries(self) -> None:
        llm = MockLLMClient("气缸故障排查步骤\n气缸报警复位方法\n设备气动系统排查")
        variants = _expand_multi_queries(llm, "气缸报警")
        assert len(variants) == 3
        assert "气缸故障排查步骤" in variants
        # 原查询不应被当成变体
        assert "气缸报警" not in variants

    def test_expand_multi_queries_ignores_noise_lines(self) -> None:
        llm = MockLLMClient("变体：\n1. 方案A\n2. 方案B\n原始查询")
        variants = _expand_multi_queries(llm, "q")
        assert variants == ["方案A", "方案B"]

    def test_expand_hyde(self) -> None:
        llm = MockLLMClient("设备气缸系统由气源、电磁阀与传感器组成，报警即排查该链路。")
        hyde = _expand_hyde(llm, "气缸报警")
        assert hyde is not None and "电磁阀" in hyde

    def test_expand_hyde_empty_returns_none(self) -> None:
        assert _expand_hyde(MockLLMClient("   \n "), "q") is None

    def test_retrieve_with_expansion_multi_merges(self) -> None:
        """multi：对每个变体再检索并并入基础召回，去重、按 id 保最优。"""
        llm = MockLLMClient("变体A\n变体B")
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5,
                            vector_candidates=1, bm25_candidates=1)
        hits_by_query = {
            "q": ([_hit(1, vec=0.9)], stats),
            "变体A": ([_hit(1, vec=0.8), _hit(2, vec=0.7)], stats),
            "变体B": ([_hit(3, vec=0.6)], stats),
        }
        retriever = MagicMock()
        retriever.search.side_effect = lambda query, **kw: hits_by_query.get(query, ([], stats))

        merged, used = _retrieve_with_expansion(
            retriever, "q", collection="t", top_k=3, mode="multi", llm_client=llm,
            base_hits=[_hit(1, vec=0.9)],
        )
        assert sorted(h.chunk.id for h in merged) == [1, 2, 3]
        # chunk 1 保留首次（更强的向量分 0.9）
        assert next(h for h in merged if h.chunk.id == 1).vector_score == 0.9
        assert used == ["变体A", "变体B"]

    def test_retrieve_with_expansion_hyde(self) -> None:
        """hyde：用假设文档检索并合并。"""
        llm = MockLLMClient("假设文档正文描述器件故障")
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5,
                            vector_candidates=1, bm25_candidates=1)
        retriever = MagicMock()
        retriever.search.return_value = ([_hit(9, vec=0.5)], stats)

        merged, used = _retrieve_with_expansion(
            retriever, "q", collection="t", top_k=3, mode="hyde",
            llm_client=llm, base_hits=[_hit(1, vec=0.9)],
        )
        assert sorted(h.chunk.id for h in merged) == [1, 9]
        assert len(used) == 1 and "假设文档" in used[0]

    def test_expansion_propagates_llm_error(self) -> None:
        """LLM 调用失败时：扩展辅助函数抛错，由调用方 try/except 降级为单查询。"""
        from doc2mind.core.llm.base import LLMError

        class BoomClient(MockLLMClient):
            def _do_chat(self, messages, temperature=None, max_tokens=None):
                raise LLMError("LLM 不可用")

        with pytest.raises(Exception):
            _retrieve_with_expansion(
                MagicMock(), "q", collection="t", top_k=3, mode="multi",
                llm_client=BoomClient(), base_hits=[],
            )

    def test_expansion_default_off(self) -> None:
        """默认 query_expansion="off"，不触发扩展调用。"""
        s = Settings()
        assert s.query_expansion == "off"
        assert Settings(query_expansion="both").query_expansion == "both"


# --- 回归：未精读网页不得进入引用；答完不得残留「正在生成」 ---
class TestWebCitationIntegrity:
    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_unfetched_web_results_are_not_cited(self) -> None:
        """content_fetched=False 的搜索命中不得进入 sources/引用计数。"""
        import json

        from doc2mind.core.search.web_search import WebSearchResult

        fetched = WebSearchResult(
            title="官方手册精读页",
            url="https://example.com/manual",
            snippet="摘要",
            source_name="Bing",
            domain="example.com",
            content="已抓取的正文内容，足够长以通过质量门槛。",
            content_fetched=True,
            relevance_score=0.8,
        )
        unfetched = WebSearchResult(
            title="未读网页",
            url="https://other.example.com/page",
            snippet="只有搜索摘要",
            source_name="Baidu",
            domain="other.example.com",
            content="",
            content_fetched=False,
            relevance_score=0.6,
        )
        mock_client = MockLLMClient("基于精读资料的回答 [1][2]")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="本地内容", source="local.pdf", page=2)
        stats = SearchStats(
            query="q", total_hits=1, elapsed_ms=5,
            vector_candidates=1, bm25_candidates=1,
        )

        class FakeWebSvc:
            def search(self, *args, **kwargs):
                return [fetched, unfetched]

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([hit], stats)
                MockRetriever.return_value = mock_retriever
                with patch(
                    "doc2mind.core.search.web_search.get_web_search_service",
                    return_value=FakeWebSvc(),
                ):
                    with patch(
                        "doc2mind.core.rag.plan_with_llm"
                    ) as mock_plan:
                        from doc2mind.core.agent.planner import AgentPlan, ToolPlan

                        mock_plan.return_value = AgentPlan(
                            analysis="知识问答",
                            query_type="question",
                            tools=[
                                ToolPlan("knowledge_base", "kb"),
                                ToolPlan("web_search", "web"),
                            ],
                            expected_output="answer",
                        )
                        from doc2mind.core.rag import rag_answer_stream

                        results = list(rag_answer_stream(
                            query="AS 与 AtomCode 关系？",
                            settings=s,
                            llm_client=mock_client,
                            enable_web_search=True,
                        ))

        done = json.loads(results[-1])
        assert done.get("done") is True
        web_srcs = [src for src in done["sources"] if src.get("source_type") == "web"]
        assert len(web_srcs) == 1
        assert web_srcs[0]["url"] == "https://example.com/manual"
        assert web_srcs[0]["content_fetched"] is True
        assert all(
            src.get("url") != "https://other.example.com/page"
            for src in done["sources"]
        )

        status_msgs = [
            json.loads(r).get("message", "")
            for r in results
            if json.loads(r).get("type") == "status"
        ]
        assert any("均未成功精读" in m or "不纳入引用" in m or "精读" in m for m in status_msgs)
        assert not any("未抓到网页正文" in json.dumps(json.loads(r), ensure_ascii=False) for r in results)

        # 自省帧只统计精读引用
        thinking_texts = [
            json.loads(r).get("text", "")
            for r in results
            if json.loads(r).get("type") == "thinking"
        ]
        assert any("1 条联网资料" in t for t in thinking_texts)
        assert not any("2 条联网资料" in t for t in thinking_texts)

    def test_all_unfetched_web_results_yield_no_web_sources(self) -> None:
        """全部未精读时：不产出 web SourceRef，状态明确说明。"""
        import json

        from doc2mind.core.search.web_search import WebSearchResult

        unfetched = WebSearchResult(
            title="未读",
            url="https://x.example.com/a",
            snippet="snippet",
            source_name="Bing",
            domain="x.example.com",
            content="",
            content_fetched=False,
        )
        mock_client = MockLLMClient("通用知识回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(
            query="q", total_hits=1, elapsed_ms=1,
            vector_candidates=1, bm25_candidates=1,
        )

        class FakeWebSvc:
            def search(self, *a, **k):
                return [unfetched]

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                MockRetriever.return_value.search.return_value = ([hit], stats)
                with patch(
                    "doc2mind.core.search.web_search.get_web_search_service",
                    return_value=FakeWebSvc(),
                ):
                    with patch("doc2mind.core.rag.plan_with_llm") as mock_plan:
                        from doc2mind.core.agent.planner import AgentPlan, ToolPlan

                        mock_plan.return_value = AgentPlan(
                            analysis="q",
                            query_type="question",
                            tools=[ToolPlan("web_search", "web")],
                            expected_output="a",
                        )
                        from doc2mind.core.rag import rag_answer_stream

                        results = list(rag_answer_stream(
                            query="问题",
                            settings=s,
                            llm_client=mock_client,
                            enable_web_search=True,
                        ))

        done = json.loads(results[-1])
        assert all(src.get("source_type") != "web" for src in done["sources"])
        status_msgs = [
            json.loads(r).get("message", "")
            for r in results
            if json.loads(r).get("type") == "status"
        ]
        assert any("均未成功精读，不纳入引用" in m for m in status_msgs)

    def test_stream_emits_completion_status_before_reflection(self) -> None:
        """生成成功后必须有「✔ 回答生成完成」，替换前端「正在生成回答...」。"""
        import json

        mock_client = MockLLMClient("完成的回答内容")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit()
        stats = SearchStats(
            query="q", total_hits=1, elapsed_ms=1,
            vector_candidates=1, bm25_candidates=1,
        )
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                MockRetriever.return_value.search.return_value = ([hit], stats)
                from doc2mind.core.rag import rag_answer_stream

                results = list(rag_answer_stream(
                    query="问题",
                    settings=s,
                    llm_client=mock_client,
                    rag_mode="hybrid",
                ))

        statuses = [
            json.loads(r)["message"]
            for r in results
            if json.loads(r).get("type") == "status"
        ]
        assert "✔ 回答生成完成" in statuses
        # 完成态应出现在 token 之后（替换「正在生成」）
        idx_done_status = next(
            i for i, r in enumerate(results)
            if json.loads(r).get("type") == "status"
            and json.loads(r).get("message") == "✔ 回答生成完成"
        )
        idx_first_token = next(
            i for i, r in enumerate(results) if "token" in json.loads(r)
        )
        assert idx_done_status > idx_first_token


class TestEvidenceSummary:
    """done 帧 / 非流式 evidence 契约：前端证据条数据源。"""

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_build_evidence_summary_counts(self) -> None:
        from doc2mind.core.rag import _build_evidence_summary

        sources = [
            SourceRef(index=1, source="a.pdf", format="pdf", source_type="local"),
            SourceRef(index=2, source="b.pdf", format="pdf", source_type="local"),
            SourceRef(
                index=3, source="t", format="web", source_type="web",
                content_fetched=True, url="https://e.com/a",
            ),
            SourceRef(
                index=4, source="u", format="web", source_type="web",
                content_fetched=False, url="https://e.com/b",
            ),
        ]
        ev = _build_evidence_summary(sources, graph_injected=True)
        assert ev["local_count"] == 2
        assert ev["web_fetched_count"] == 1
        assert ev["web_unfetched_count"] == 1
        assert ev["graph_injected"] is True
        assert ev["fallback_general_knowledge"] is False

    def test_build_evidence_summary_fallback(self) -> None:
        from doc2mind.core.rag import _build_evidence_summary

        ev = _build_evidence_summary([], fallback_general_knowledge=True)
        assert ev["local_count"] == 0
        assert ev["web_fetched_count"] == 0
        assert ev["fallback_general_knowledge"] is True

    def test_stream_local_hit_evidence(self) -> None:
        import json

        mock_client = MockLLMClient("有依据的回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="本地内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                MockRetriever.return_value.search.return_value = ([hit], stats)
                results = list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                ))
        done = json.loads(results[-1])
        assert done["done"] is True
        ev = done["evidence"]
        assert ev["local_count"] == 1
        assert ev["web_fetched_count"] == 0
        assert ev["fallback_general_knowledge"] is False
        assert ev["graph_injected"] is False

    def test_stream_fallback_general_knowledge_evidence(self) -> None:
        """空检索 hybrid 路径：fallback_general_knowledge=true。"""
        import json

        mock_client = MockLLMClient("💡 本地知识库未命中直接依据，以下基于通用知识为您解答：回答内容")
        s = Settings(llm_provider="openai", llm_api_key="test")
        stats = SearchStats(query="q", total_hits=0, elapsed_ms=5, vector_candidates=0, bm25_candidates=0)
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                MockRetriever.return_value.search.return_value = ([], stats)
                results = list(rag_answer_stream(
                    query="库外问题", settings=s, llm_client=mock_client, rag_mode="hybrid",
                ))
        done = json.loads(results[-1])
        ev = done["evidence"]
        assert ev["local_count"] == 0
        assert ev["fallback_general_knowledge"] is True

    def test_stream_strict_no_hit_evidence_not_fallback(self) -> None:
        """strict 早返回：evidence 全零且 fallback=false（拒绝作答，非通用知识）。"""
        import json

        mock_client = MockLLMClient("不应被调用")
        s = Settings(llm_provider="openai", llm_api_key="test", rag_mode="strict")
        stats = SearchStats(query="q", total_hits=0, elapsed_ms=5, vector_candidates=0, bm25_candidates=0)
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                MockRetriever.return_value.search.return_value = ([], stats)
                results = list(rag_answer_stream(
                    query="库外问题", settings=s, llm_client=mock_client,
                ))
        done = next(f for f in (json.loads(r) for r in results) if f.get("done") is True)
        ev = done["evidence"]
        assert ev["local_count"] == 0
        assert ev["fallback_general_knowledge"] is False

    def test_stream_graph_entity_context_evidence(self) -> None:
        import json

        mock_client = MockLLMClient("结合图谱的回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="本地内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                MockRetriever.return_value.search.return_value = ([hit], stats)
                results = list(rag_answer_stream(
                    query="实体问题", settings=s, llm_client=mock_client,
                    entity_context="实体【Agent】 --[relies_on]--> 实体【ToolCall】",
                ))
        done = json.loads(results[-1])
        ev = done["evidence"]
        assert ev["graph_injected"] is True
        assert ev["local_count"] == 1

    def test_non_stream_rag_answer_carries_evidence(self) -> None:
        mock_client = MockLLMClient("非流式回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        hit = _make_hit(content="本地内容", source="doc.pdf", page=1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                MockRetriever.return_value.search.return_value = ([hit], stats)
                answer = rag_answer(query="问题", settings=s, llm_client=mock_client)
        assert answer.evidence["local_count"] == 1
        assert answer.evidence["fallback_general_knowledge"] is False

    def test_history_sources_json_roundtrip_keeps_evidence_fields(self, tmp_path) -> None:
        """sources_json 落库往返不丢 content_fetched/score_type/evidence_level 等字段。"""
        import json

        from doc2mind.core.rag import _append_turn
        from doc2mind.core.store.chat_store import ChatStore

        db = tmp_path / "hist.db"
        sources = [
            SourceRef(
                index=1, source="a.pdf", format="pdf", chunk_id=7, page=3,
                score=0.9, score_type="rerank", source_type="local",
                snippet="本地切片",
            ),
            SourceRef(
                index=2, source="t", format="web", source_type="web",
                url="https://e.com/a", title="网页", snippet="s",
                content_fetched=True, evidence_level="多来源共识",
                corroborated_by=2, domain="e.com",
            ),
        ]
        _append_turn("chat-hist", "问题", "回答", db, sources=sources)
        store = ChatStore(db)
        msgs = store.get_messages("chat-hist")
        assistant = next(m for m in msgs if m.role == "assistant")
        parsed = json.loads(assistant.sources_json)
        assert parsed[0]["content_fetched"] is False
        assert parsed[0]["score_type"] == "rerank"
        assert parsed[0]["chunk_id"] == 7
        assert parsed[1]["content_fetched"] is True
        assert parsed[1]["evidence_level"] == "多来源共识"
        assert parsed[1]["url"] == "https://e.com/a"


class TestUnderlyingCapability:
    """底层能力：主题锚定 / 弱模型瘦身 / 记忆不污染检索 / 弱命中净化。"""

    def test_has_distinctive_topic(self) -> None:
        from doc2mind.core.rag import _has_distinctive_topic

        assert _has_distinctive_topic("什么是GPT") is True
        assert _has_distinctive_topic("nemotron 性能如何") is True
        assert _has_distinctive_topic("ISO1940 标准") is True
        # 2026-09-13 豆包问答截图：纯中文专名（Latin 部分 ai 仅 2 字母）
        assert _has_distinctive_topic("什么是豆包ai") is True
        assert _has_distinctive_topic("介绍一下豆包") is True
        assert _has_distinctive_topic("什么是架构？") is False
        assert _has_distinctive_topic("测试问题") is False
        assert _has_distinctive_topic("报错了怎么修") is False

    def test_filter_topic_aligned_hits_drops_off_topic(self) -> None:
        from doc2mind.core.rag import _filter_topic_aligned_hits

        guide = _make_hit(content="DocMind 快速上手：点击新建对话导入文档。", score=0.7)
        real = _make_hit(content="GPT（Generative Pre-trained Transformer）是大语言模型。", score=0.6)
        aligned, dropped = _filter_topic_aligned_hits("什么是GPT", [guide, real])
        assert aligned == [real]
        assert dropped == [guide]

    def test_filter_topic_aligned_hits_noop_for_generic_chinese(self) -> None:
        from doc2mind.core.rag import _filter_topic_aligned_hits

        hit = _make_hit(content="测试内容", score=0.8)
        aligned, dropped = _filter_topic_aligned_hits("什么是架构？", [hit])
        assert aligned == [hit]
        assert dropped == []

    def test_llm_filter_web_results_keeps_relevant(self) -> None:
        from doc2mind.core.rag import _llm_filter_web_results

        class Web:
            def __init__(self, title, domain="e.com", snippet=""):
                self.title = title
                self.domain = domain
                self.snippet = snippet

        junk = Web("在线小游戏合集", snippet="免费小游戏")
        good = Web("GPT - Wikipedia", domain="en.wikipedia.org", snippet="Generative Pre-trained Transformer")
        client = MockLLMClient("1,2")  # 会返回固定 reply；改用专用 mock

        class PickMock(MockLLMClient):
            def _do_chat(self, messages, temperature=None, max_tokens=None):
                self.last_messages = messages
                return "2"

        kept = _llm_filter_web_results("什么是GPT", [junk, good], PickMock("x"))
        assert kept == [good]

    def test_llm_filter_web_results_fallback_on_error(self) -> None:
        from doc2mind.core.rag import _llm_filter_web_results

        class Web:
            title = "t"
            domain = "d"
            snippet = "s"

        class Boom(MockLLMClient):
            def _do_chat(self, messages, temperature=None, max_tokens=None):
                raise RuntimeError("llm down")

        results = [Web() for _ in range(5)]
        kept = _llm_filter_web_results("q", results, Boom("x"), max_keep=3)
        assert kept == results[:3]

    def test_llm_filter_web_results_zero_falls_back_not_empty(self) -> None:
        """截图故障：LLM 输出 0 不得清空引用，必须回退规则排序结果。"""
        from doc2mind.core.rag import _llm_filter_web_results

        class Web:
            def __init__(self, title, content=""):
                self.title = title
                self.domain = "baike.baidu.com"
                self.snippet = ""
                self.content = content
                self.content_fetched = bool(content)

        results = [
            Web("阻尼_百度百科", "阻尼是指振动过程中能量耗散的机制。"),
            Web("对阻尼的理解 - 知乎", "阻尼用于描述系统振荡衰减。"),
            Web("阻尼有哪些类型? - 知乎", "欠阻尼、过阻尼、临界阻尼。"),
        ]

        class SayZero(MockLLMClient):
            def _do_chat(self, messages, temperature=None, max_tokens=None):
                return "0"

        kept = _llm_filter_web_results("什么是阻尼", results, SayZero("x"))
        assert kept == results  # 全部回退，禁止 0 条

    def test_llm_filter_keeps_title_matching_core_token(self) -> None:
        """LLM 漏选时，标题含问题核心词的条目强制保留。"""
        from doc2mind.core.rag import _llm_filter_web_results

        class Web:
            def __init__(self, title, snippet=""):
                self.title = title
                self.domain = "e.com"
                self.snippet = snippet
                self.content = ""

        wiki = Web("阻尼_百度百科", "振动能量耗散")
        other = Web("广告位招租", "无关")
        results = [wiki, other]

        class PickOnlyOther(MockLLMClient):
            def _do_chat(self, messages, temperature=None, max_tokens=None):
                return "2"

        kept = _llm_filter_web_results("什么是阻尼", results, PickOnlyOther("x"))
        assert wiki in kept
        assert other in kept

    def test_is_weak_model_detects_nemotron(self) -> None:
        from doc2mind.core.rag import is_weak_model

        assert is_weak_model("nvidia/nemotron-3-super-120b-a12b") is True
        assert is_weak_model("gpt-4o") is False
        assert is_weak_model(None) is False

    def test_hits_match_topic(self) -> None:
        from doc2mind.core.rag import _hits_match_topic

        gpt_hit = _make_hit(content="DocMind 快速上手：点击新建对话，导入文档后即可检索。", score=0.9)
        doubao_hit = _make_hit(content="豆包（Doubao）是字节跳动旗下的大语言模型。", score=0.9)
        assert _hits_match_topic("你知道gpt吗", [gpt_hit]) is False
        assert _hits_match_topic("介绍一下豆包", [doubao_hit]) is True
        # 无显著 token（空/纯语气）时不误杀
        assert _hits_match_topic("", [gpt_hit]) is True
        assert _hits_match_topic("嗯", [gpt_hit]) is True

    def test_system_prompt_has_subject_anchor(self) -> None:
        from doc2mind.core.rag import _SYSTEM_PROMPT, _SYSTEM_PROMPT_SLIM

        assert "主题锚定" in _SYSTEM_PROMPT
        assert "主题锚定" in _SYSTEM_PROMPT_SLIM
        # 瘦版不含 ACTIONS 表演
        assert "ACTIONS" not in _SYSTEM_PROMPT_SLIM

    def test_slim_prompt_has_tiered_length_rule(self) -> None:
        """瘦版提示词篇幅指令与完整版等价分层，不再一刀切压缩。"""
        from doc2mind.core.rag import _SYSTEM_PROMPT, _SYSTEM_PROMPT_SLIM

        # 四档分层关键字（与完整版【回答篇幅原则】语义一致）
        assert "中等问题" in _SYSTEM_PROMPT_SLIM
        assert "300-500" in _SYSTEM_PROMPT_SLIM
        assert "复杂分析" in _SYSTEM_PROMPT_SLIM
        assert "一段话直接回答" in _SYSTEM_PROMPT_SLIM
        # 一刀切压缩指令已移除
        assert "简洁作答，简单问题一段话" not in _SYSTEM_PROMPT_SLIM
        # 与完整版篇幅原则保持一致口径
        assert "【回答篇幅原则】" in _SYSTEM_PROMPT
        assert "300-500" in _SYSTEM_PROMPT

    def test_slim_prompt_tiered_length_anchor_retained(self) -> None:
        """弱模型瘦版提示词仍以主题锚定开头、保留硬规则结构。"""
        from doc2mind.core.rag import _SYSTEM_PROMPT_SLIM

        assert _SYSTEM_PROMPT_SLIM.startswith("【主题锚定")
        assert "硬规则" in _SYSTEM_PROMPT_SLIM
        assert "【回答篇幅原则】" in _SYSTEM_PROMPT_SLIM
        assert "ACTIONS" not in _SYSTEM_PROMPT_SLIM

    def test_memory_context_injected_not_in_retrieval_query(self) -> None:
        """记忆作为独立块注入；retriever.search 收到的 query 必须干净。"""
        from doc2mind.core.config import Settings

        captured: dict = {}

        class _CaptureRetriever:
            def __init__(self, **kwargs):
                pass

            def search(self, query, collection=None, top_k=5, min_score=0.0):
                captured["query"] = query
                return [], SearchStats(
                    query=query, total_hits=0, elapsed_ms=1,
                    vector_candidates=0, bm25_candidates=0, reranked=False,
                )

        s = Settings(rag_mode="hybrid")
        client = MockLLMClient("基于通用知识回答。")
        with (
            patch("doc2mind.core.rag.Retriever", _CaptureRetriever),
            patch("doc2mind.core.rag._open_store") as open_store,
            patch("doc2mind.core.rag.get_reranker", return_value=None),
            patch("doc2mind.core.store.graph_store.GraphStore") as gs,
        ):
            open_store.return_value = (MagicMock(), MagicMock())
            gs.return_value.find_entities_by_keyword.return_value = []
            answer = rag_answer(
                "你知道gpt吗",
                settings=s,
                llm_client=client,
                enable_web_search=False,
                memory_context="- 豆包是字节跳动的大模型",
            )
        assert captured["query"] == "你知道gpt吗"
        assert "[用户记忆]" not in captured["query"]
        # 记忆进入 LLM 消息，但以独立框定出现
        user_msgs = [m for m in client.last_messages if m.get("role") == "user"]
        assert any("豆包" in m.get("content", "") for m in user_msgs)
        assert any("不是本轮问题主题" in m.get("content", "") for m in user_msgs)
        assert answer.answer

    def test_weak_model_uses_slim_prompt(self) -> None:
        from doc2mind.core.config import Settings

        s = Settings(rag_mode="hybrid")
        client = MockLLMClient("简短回答。")
        client._model_override = "nvidia/nemotron-3-super-120b-a12b"

        class _NemotronClient(MockLLMClient):
            @property
            def model_name(self) -> str:
                return "nvidia/nemotron-3-super-120b-a12b"

        client = _NemotronClient("简短回答。")

        class _EmptyRetriever:
            def __init__(self, **kwargs):
                pass

            def search(self, query, collection=None, top_k=5, min_score=0.0):
                return [], SearchStats(
                    query=query, total_hits=0, elapsed_ms=1,
                    vector_candidates=0, bm25_candidates=0, reranked=False,
                )

        with (
            patch("doc2mind.core.rag.Retriever", _EmptyRetriever),
            patch("doc2mind.core.rag._open_store") as open_store,
            patch("doc2mind.core.rag.get_reranker", return_value=None),
            patch("doc2mind.core.store.graph_store.GraphStore") as gs,
        ):
            open_store.return_value = (MagicMock(), MagicMock())
            gs.return_value.find_entities_by_keyword.return_value = []
            rag_answer("什么是GPT", settings=s, llm_client=client, enable_web_search=False)

        system = client.last_messages[0]["content"]
        assert "主题锚定" in system
        assert "硬规则" in system
        assert "ACTIONS" not in system

    def test_very_weak_local_hits_not_cited_without_web(self) -> None:
        """无联网时，极弱本地命中不进 sources，交给 hybrid。"""
        from doc2mind.core.config import Settings

        class _WeakRetriever:
            def __init__(self, **kwargs):
                pass

            def search(self, query, collection=None, top_k=5, min_score=0.0):
                hit = _make_hit(
                    content="DocMind 操作：点击左侧知识库图标管理集合。",
                    score=0.15,
                )
                # 压低分量分，使 local_max_rel < 0.30
                hit = SearchHit(
                    chunk=hit.chunk, score=0.15, match_type="vector",
                    vector_score=0.15, bm25_score=0.1, rank=1, rerank_score=0.2,
                )
                return [hit], SearchStats(
                    query=query, total_hits=1, elapsed_ms=1,
                    vector_candidates=1, bm25_candidates=1, reranked=True,
                )

        s = Settings(rag_mode="hybrid")
        client = MockLLMClient("GPT 是 OpenAI 的大语言模型。")
        with (
            patch("doc2mind.core.rag.Retriever", _WeakRetriever),
            patch("doc2mind.core.rag._open_store") as open_store,
            patch("doc2mind.core.rag.get_reranker", return_value=None),
            patch("doc2mind.core.store.graph_store.GraphStore") as gs,
        ):
            open_store.return_value = (MagicMock(), MagicMock())
            gs.return_value.find_entities_by_keyword.return_value = []
            answer = rag_answer(
                "什么是GPT",
                settings=s,
                llm_client=client,
                enable_web_search=False,
            )
        # 弱命中不进引用列表
        assert all(sref.source_type != "local" for sref in answer.sources)
        system = client.last_messages[0]["content"]
        assert "未命中" in system or "通用知识" in system or "主题锚定" in system

class TestResolveMaxTokensWiring:
    """调用层 max_tokens 推导接入验证（tasks T4.3 / 验收 A6/A10/A12/A13）。"""

    @staticmethod
    def _run(client, settings, metadata_provider_factory=None) -> None:
        class _EmptyRetriever:
            def __init__(self, **kwargs):
                pass

            def search(self, query, collection=None, top_k=5, min_score=0.0):
                return [], SearchStats(
                    query=query, total_hits=0, elapsed_ms=1,
                    vector_candidates=0, bm25_candidates=0, reranked=False,
                )

        patches = [
            patch("doc2mind.core.rag.Retriever", _EmptyRetriever),
            patch("doc2mind.core.rag._open_store"),
            patch("doc2mind.core.rag.get_reranker", return_value=None),
            patch("doc2mind.core.store.graph_store.GraphStore"),
            patch(
                "doc2mind.core.rag._build_metadata_provider",
                side_effect=metadata_provider_factory or (lambda c: None),
            ),
        ]
        with patches[0], patches[1] as open_store, patches[2], patches[3] as gs, patches[4]:
            open_store.return_value = (MagicMock(), MagicMock())
            gs.return_value.find_entities_by_keyword.return_value = []
            rag_answer(
                "什么是GPT", settings=settings, llm_client=client, enable_web_search=False,
            )

    @staticmethod
    def _recording_client(reply="GPT 是 OpenAI 的大语言模型。") -> "MockLLMClient":
        class _Rec(MockLLMClient):
            @property
            def model_name(self) -> str:
                return "nvidia/nemotron-3-super-120b-a12b"

            @property
            def provider(self) -> str:
                return "openai"

            def _do_chat(self, messages, temperature=None, max_tokens=None):
                self.last_max_tokens = max_tokens
                self.last_messages = messages
                return self._reply

        return _Rec(reply)

    def test_no_metadata_falls_to_registry(self) -> None:
        """无元数据 provider 时，max_tokens 取 registry 推导值（A11 降级）。"""
        from doc2mind.core.config import Settings

        class _Rec(MockLLMClient):
            @property
            def model_name(self) -> str:
                return "nvidia/nemotron-3-super-120b-a12b"

            @property
            def provider(self) -> str:
                return "openai"

            def _do_chat(self, messages, temperature=None, max_tokens=None):
                self.last_max_tokens = max_tokens
                self.last_messages = messages
                return self._reply

        client = _Rec("GPT 是 OpenAI 的大语言模型。")
        s = Settings(rag_mode="hybrid", llm_max_tokens=0)  # 0 视为未配置，走降级链
        self._run(client, s, metadata_provider_factory=lambda c: None)
        # nemotron 不在已知列表，registry fallback(openai)=8192
        assert client.last_max_tokens == 8192

    def test_metadata_available_lifts_limit(self) -> None:
        """元数据声明输出上限 16384 时，max_tokens 高于 8192（A10）。"""
        from doc2mind.core.config import Settings
        from doc2mind.core.llm.metadata import ModelMetadata, ModelMetadataProvider

        class _Stub(ModelMetadataProvider):
            def fetch(self, model_name: str, timeout: float = 3.0):
                return ModelMetadata(
                    model_id=model_name,
                    context_length=131072,
                    max_output_tokens=16384,
                    source_endpoint="/v1/models",
                )

        class _Rec(MockLLMClient):
            @property
            def model_name(self) -> str:
                return "nvidia/nemotron-3-super-120b-a12b"

            @property
            def provider(self) -> str:
                return "openai"

            def _do_chat(self, messages, temperature=None, max_tokens=None):
                self.last_max_tokens = max_tokens
                self.last_messages = messages
                return self._reply

        client = _Rec("GPT 是 OpenAI 的大语言模型。")
        s = Settings(rag_mode="hybrid", llm_max_tokens=0)
        self._run(client, s, metadata_provider_factory=lambda c: _Stub())
        assert client.last_max_tokens == 16384

    def test_user_config_priority_over_metadata(self) -> None:
        """user_config 显式 4096 优先于元数据声明值（A13）。"""
        from doc2mind.core.config import Settings
        from doc2mind.core.llm.metadata import ModelMetadata, ModelMetadataProvider

        class _Stub(ModelMetadataProvider):
            def fetch(self, model_name: str, timeout: float = 3.0):
                return ModelMetadata(
                    model_id=model_name,
                    context_length=131072,
                    max_output_tokens=16384,
                    source_endpoint="/v1/models",
                )

        class _Rec(MockLLMClient):
            @property
            def model_name(self) -> str:
                return "nvidia/nemotron-3-super-120b-a12b"

            @property
            def provider(self) -> str:
                return "openai"

            def _do_chat(self, messages, temperature=None, max_tokens=None):
                self.last_max_tokens = max_tokens
                self.last_messages = messages
                return self._reply

        client = _Rec("GPT 是 OpenAI 的大语言模型。")
        s = Settings(rag_mode="hybrid", llm_max_tokens=4096)
        self._run(client, s, metadata_provider_factory=lambda c: _Stub())
        assert client.last_max_tokens == 4096

    def test_source_log_recorded(self, caplog) -> None:
        """来源链日志可查（A12）：含「输出上限=」与「来源=」。"""
        import logging

        from doc2mind.core.config import Settings

        class _Rec(MockLLMClient):
            @property
            def model_name(self) -> str:
                return "nvidia/nemotron-3-super-120b-a12b"

            @property
            def provider(self) -> str:
                return "openai"

            def _do_chat(self, messages, temperature=None, max_tokens=None):
                self.last_max_tokens = max_tokens
                self.last_messages = messages
                return self._reply

        client = _Rec("GPT 是 OpenAI 的大语言模型。")
        s = Settings(rag_mode="hybrid", llm_max_tokens=0)
        with caplog.at_level(logging.INFO, logger="doc2mind.core.rag"):
            self._run(client, s, metadata_provider_factory=lambda c: None)
        joined = "\n".join(r.message for r in caplog.records)
        assert "输出上限=" in joined
        assert "来源=" in joined
