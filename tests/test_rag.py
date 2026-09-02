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
    _format_source_ref,
    _load_history,
    _max_history,
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
        # status 帧全部在第一个 token 帧之前
        first_token_idx = next(i for i, f in enumerate(all_frames) if "token" in f)
        for sf in status_frames:
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
