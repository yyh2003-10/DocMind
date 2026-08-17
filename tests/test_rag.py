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
    _format_source_ref,
    _load_history,
    _save_history,
    clear_session,
    rag_answer,
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
                r2 = rag_answer(query="那组件呢？", chat_id=cid, settings=s, llm_client=mock_client)

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
        """默认 rag_min_score=0.0 不过滤任何命中（回归保护）。"""
        mock_client = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test")  # rag_min_score 默认 0.0
        low = _make_hit(content="低分噪声", score=0.1)
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_store = MagicMock()
            mock_embedder = MagicMock()
            mock_open.return_value = (mock_store, mock_embedder)

            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([low], stats)
                MockRetriever.return_value = mock_retriever

                result = rag_answer(query="问题", settings=s, llm_client=mock_client)

            assert len(result.sources) == 1  # 低分命中仍被保留

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

                from doc2mind.core.rag import rag_answer_stream
                import json
                results = list(rag_answer_stream(
                    query="问题", settings=s, llm_client=mock_client,
                ))

        # 检查 token 行
        assert len(results) >= 2  # 至少 token + done
        token_data = json.loads(results[0])
        assert "token" in token_data
        assert token_data["token"] == "流式回答"

        # 检查终帧
        done_data = json.loads(results[-1])
        assert done_data.get("done") is True
        assert done_data["chat_id"] is not None
        assert done_data["model"] == "mock-model"
        assert done_data["total_chunks"] == 1

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

        assert len(results) == 2  # 提示 token + done
        token_data = json.loads(results[0])
        assert "未找到" in token_data["token"]
        done_data = json.loads(results[1])
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

    def test_stream_yields_token_per_chunk(self) -> None:
        """子类逐 token 流式实现：每个 chunk 一帧，按序拼接还原完整回答。"""
        import json

        class ChunkedMockClient(MockLLMClient):
            def _do_stream_chat(self, messages, temperature=None, max_tokens=None):
                for piece in ["根据", "资料", "回答。"]:
                    yield piece

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

        token_frames = [json.loads(r) for r in results[:-1]]
        assert "".join(f["token"] for f in token_frames) == "根据资料回答。"
        # 历史已保存完整拼接的回答
        cid = json.loads(results[-1])["chat_id"]
        from doc2mind.core.rag import _CHAT_SESSIONS as sessions
        assert sessions[cid][-1] == {"role": "assistant", "content": "根据资料回答。"}


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
