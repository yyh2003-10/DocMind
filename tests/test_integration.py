"""集成测试 — HTTP /v1/chat、MCP chat 工具、并发 session 安全。

全部使用 mock，不依赖真实 LLM 或向量库。
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from doc2mind.core.config import Settings, get_settings, set_settings
from doc2mind.core.llm.base import LLMClient
from doc2mind.core.rag import (
    _CHAT_SESSIONS,
    _save_history,
    rag_answer,
)
from doc2mind.core.retriever.search import SearchHit, SearchStats, StoredChunkMeta


# --- Shared Mock LLM ---
class MockLLMClient(LLMClient):
    def __init__(self, reply: str = "集成测试回答。") -> None:
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


def _make_hit(
    content: str = "测试内容",
    source: str = "test.pdf",
    page: int | None = 1,
    heading: str | None = None,
    score: float = 0.8,
) -> SearchHit:
    chunk = StoredChunkMeta(
        id=1, content=content, source=source, format="pdf",
        doc_type=None, page=page, heading=heading, tokens=100,
        chunk_index=0, collection="default",
    )
    return SearchHit(
        chunk=chunk, score=score, match_type="hybrid",
        vector_score=score, bm25_score=score * 0.9, rank=0,
    )


def _mock_rag_patches(mock_llm: MockLLMClient, hit: SearchHit | None = None):
    """返回一个 context manager stack，mock 掉 _open_store、Retriever、get_llm_client。"""
    from contextlib import ExitStack
    stack = ExitStack()

    if hit is None:
        hit = _make_hit()
    stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)

    mock_store = MagicMock()
    mock_embedder = MagicMock()
    stack.enter_context(patch("doc2mind.core.rag._open_store", return_value=(mock_store, mock_embedder)))

    mock_retriever = MagicMock()
    mock_retriever.search.return_value = ([hit], stats)
    stack.enter_context(patch("doc2mind.core.rag.Retriever", return_value=mock_retriever))
    stack.enter_context(patch("doc2mind.core.rag.get_llm_client", return_value=mock_llm))

    return stack


# ======================================================================
# 1. HTTP POST /v1/chat 集成测试
# ======================================================================
class TestHTTPChatEndpoint:
    """通过 FastAPI TestClient 测试 /v1/chat 端点。"""

    @pytest.fixture()
    def client_with_app(self):
        """创建 FastAPI app 并注入 mock LLM。"""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/testclient not installed")

        mock_llm = MockLLMClient("根据文档，DocMind 采用分层架构。")
        hit = _make_hit(content="DocMind 采用分层架构...", source="arch.pdf", page=3, heading="架构")

        stack = _mock_rag_patches(mock_llm, hit)
        try:
            from doc2mind.server.http import create_app
            app = create_app()
            test_client = TestClient(app)
            yield test_client, mock_llm
        finally:
            stack.close()

    def test_chat_returns_answer(self, client_with_app) -> None:
        tc, mock_llm = client_with_app
        resp = tc.post("/v1/chat", json={"query": "架构是什么？"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "根据文档，DocMind 采用分层架构。"
        assert data["chat_id"]  # 非空
        assert data["model"] == "mock-model"
        assert data["provider"] == "mock"
        assert len(data["sources"]) == 1
        assert data["sources"][0]["source"] == "arch.pdf"
        assert data["sources"][0]["page"] == 3

    def test_chat_empty_query_returns_422(self, client_with_app) -> None:
        tc, _ = client_with_app
        resp = tc.post("/v1/chat", json={})
        assert resp.status_code == 422  # validation error

    def test_chat_with_chat_id_maintains_session(self, client_with_app) -> None:
        tc, mock_llm = client_with_app
        # 第一轮
        resp1 = tc.post("/v1/chat", json={"query": "什么是架构？"})
        cid = resp1.json()["chat_id"]

        # 第二轮（传入 chat_id）
        resp2 = tc.post("/v1/chat", json={"query": "那组件呢？", "chat_id": cid})
        assert resp2.status_code == 200
        assert resp2.json()["chat_id"] == cid

    def test_chat_accepts_collections_field(self, client_with_app) -> None:
        """多选知识库：请求体带 collections 列表不应 422，且透传检索。"""
        tc, mock_llm = client_with_app
        resp = tc.post("/v1/chat", json={
            "query": "架构是什么？",
            "collections": ["docs-a", "docs-b"],
        })
        assert resp.status_code == 200
        assert resp.json()["answer"] == "根据文档，DocMind 采用分层架构。"

    def test_chat_collections_string_rejected(self, client_with_app) -> None:
        """collections 必须是数组；传字符串应 422（接口契约校验）。"""
        tc, _ = client_with_app
        resp = tc.post("/v1/chat", json={"query": "q", "collections": "docs-a"})
        assert resp.status_code == 422

    def test_chat_stream_returns_sse(self, client_with_app) -> None:
        """流式对话返回 SSE 格式，包含 token 行和终帧。"""
        tc, mock_llm = client_with_app
        with tc.stream("POST", "/v1/chat/stream", json={"query": "架构是什么？"}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = [line for line in response.iter_lines() if line]
            # 过滤 SSE 前缀
            events = [l.replace("data: ", "") for l in lines if l.startswith("data: ")]
            assert len(events) >= 2
            import json
            first = json.loads(events[0])
            # 可能是 token 行或 error 行
            assert "token" in first or "error" in first


# ======================================================================
# 2. MCP chat 工具集成测试
# ======================================================================
class TestMCPChatTool:
    """测试 MCP _tool_chat 分派。"""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _CHAT_SESSIONS.clear()
        yield
        _CHAT_SESSIONS.clear()

    def test_tool_chat_returns_ok(self) -> None:
        mock_llm = MockLLMClient("MCP 测试回答。")
        stack = _mock_rag_patches(mock_llm)
        try:
            from doc2mind.server.mcp import _dispatch_tool
            result = json.loads(_dispatch_tool("chat", {"query": "测试问题"}))
        finally:
            stack.close()

        assert "result" in result
        assert result["result"]["answer"]
        assert result["result"]["chat_id"]
        assert result["result"]["model"] == "mock-model"
        assert len(result["result"]["sources"]) == 1

    def test_tool_chat_with_chat_id(self) -> None:
        mock_llm = MockLLMClient("MCP 回答。")
        stack = _mock_rag_patches(mock_llm)
        try:
            from doc2mind.server.mcp import _dispatch_tool
            r1 = json.loads(_dispatch_tool("chat", {"query": "第一轮"}))
            cid = r1["result"]["chat_id"]

            r2 = json.loads(_dispatch_tool("chat", {"query": "第二轮", "chat_id": cid}))
            assert r2["result"]["chat_id"] == cid
        finally:
            stack.close()

    def test_tool_chat_no_llm_returns_error(self) -> None:
        """未配置 LLM 时 MCP 返回 RAG_ERROR。"""
        from doc2mind.server.mcp import _dispatch_tool
        s = Settings(llm_provider="none")
        with patch("doc2mind.core.rag.get_settings", return_value=s):
            result = json.loads(_dispatch_tool("chat", {"query": "测试"}))
        assert "error" in result
        assert result["error"]["code"] == "RAG_ERROR"
        assert "未配置 LLM" in result["error"]["message"]


# ======================================================================
# 4. HTTP GET/POST /v1/config 的 LLM 字段
# ======================================================================
class TestHTTPConfigLLMFields:
    """设置页依赖的 LLM/RAG 配置读写契约。

    - GET 返回全部 LLM 字段 + llm_api_key_configured 标志
    - POST 更新运行时 settings（保存即生效）
    - API Key 绝不出现在任何响应体中
    """

    @pytest.fixture()
    def config_client(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/testclient not installed")

        original = get_settings()
        set_settings(Settings())  # 干净默认值，隔离全局单例
        try:
            # 不写真实 config.toml（endpoint 内部延迟 import，patch 源模块即可）
            with patch("doc2mind.core.config.save_settings"):
                from doc2mind.server.http import create_app
                app = create_app()
                yield TestClient(app)
        finally:
            set_settings(original)  # 恢复全局单例

    def test_get_config_returns_llm_defaults(self, config_client) -> None:
        resp = config_client.get("/v1/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_provider"] == "none"
        assert data["llm_base_url"] is None
        assert data["llm_model"] == ""
        assert data["llm_temperature"] == 0.7
        assert data["llm_max_tokens"] == 2048
        assert data["rag_top_k"] == 5
        assert data["rag_min_score"] == 0.0
        assert data["llm_api_key_configured"] is False

    def test_post_config_updates_llm_fields(self, config_client) -> None:
        resp = config_client.post("/v1/config", json={
            "llm_provider": "openai",
            "llm_base_url": "https://api.deepseek.com/v1",
            "llm_model": "deepseek-chat",
            "llm_temperature": 0.3,
            "llm_max_tokens": 1024,
            "rag_top_k": 8,
            "rag_min_score": 0.2,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_provider"] == "openai"
        assert data["llm_base_url"] == "https://api.deepseek.com/v1"
        assert data["llm_model"] == "deepseek-chat"
        assert data["llm_temperature"] == 0.3
        assert data["llm_max_tokens"] == 1024
        assert data["rag_top_k"] == 8
        assert data["rag_min_score"] == 0.2
        # 更新生效到运行时 settings（保存即推送）
        s = get_settings()
        assert s.llm_provider == "openai"
        assert s.llm_model == "deepseek-chat"
        assert s.llm_temperature == 0.3
        assert s.rag_top_k == 8

    def test_post_config_api_key_never_echoed(self, config_client) -> None:
        """API Key 只能通过 llm_api_key_configured 感知，响应体不得含明文。"""
        resp = config_client.post("/v1/config", json={
            "llm_provider": "openai",
            "llm_api_key": "sk-secret-do-not-leak",
        })
        assert resp.status_code == 200
        assert "sk-secret-do-not-leak" not in resp.text
        assert resp.json()["llm_api_key_configured"] is True
        # 运行时已保存（供后端调用 LLM），但 GET 也不回传
        resp2 = config_client.get("/v1/config")
        assert "sk-secret-do-not-leak" not in resp2.text
        assert resp2.json()["llm_api_key_configured"] is True

    def test_post_config_ollama_provider(self, config_client) -> None:
        resp = config_client.post("/v1/config", json={
            "llm_provider": "ollama",
            "llm_model": "llama3.2",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_provider"] == "ollama"
        assert data["llm_model"] == "llama3.2"


# ======================================================================
# 5. 并发 session 写入安全测试
# ======================================================================
class TestConcurrentSessionSafety:
    """多线程并发写入 session 不应导致数据丢失或异常。"""

    def setup_method(self) -> None:
        _CHAT_SESSIONS.clear()

    def test_concurrent_save_history(self) -> None:
        """多线程同时保存历史到同一 chat_id，不丢数据。"""
        chat_id = "concurrent-test"
        errors: list[Exception] = []

        def writer(thread_id: int) -> None:
            try:
                for i in range(20):
                    msgs = [{"role": "user", "content": f"t{thread_id}-q{i}"}]
                    _save_history(chat_id, msgs)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"并发写入异常: {errors}"
        assert chat_id in _CHAT_SESSIONS
        assert len(_CHAT_SESSIONS[chat_id]) > 0

    def test_concurrent_different_sessions(self) -> None:
        """多线程写入不同 chat_id，互不干扰。"""
        errors: list[Exception] = []

        def writer(chat_id: str) -> None:
            try:
                for i in range(10):
                    _save_history(chat_id, [{"role": "user", "content": f"q{i}"}])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"session-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"并发写入异常: {errors}"
        assert len(_CHAT_SESSIONS) == 10

    def test_concurrent_rag_answer(self) -> None:
        """多线程并发调用 rag_answer，不产生竞争条件。

        注意：patch 必须在主线程一次性建立。若每个线程各自 patch 同一批
        模块属性（_open_store / Retriever / get_llm_client），patch 保存的
        "原值"会在重叠线程间互相污染，退出后可能残留别的线程的 mock，
        泄漏到后续测试（此前偶发 FakeService 未实现等连锁失败）。
        """
        errors: list[Exception] = []
        results: list[str] = []
        chat_ids: set[str] = set()

        mock_llm = MockLLMClient("回答")
        s = Settings(llm_provider="openai", llm_api_key="test")
        stack = _mock_rag_patches(mock_llm)

        def ask(thread_id: int) -> None:
            try:
                answer = rag_answer(
                    query=f"问题-{thread_id}",
                    settings=s,
                    llm_client=mock_llm,
                )
                results.append(answer.answer)
                chat_ids.add(answer.chat_id)
            except Exception as e:
                errors.append(e)

        try:
            threads = [threading.Thread(target=ask, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        finally:
            stack.close()

        assert not errors, f"并发 RAG 异常: {errors}"
        assert len(results) == 8
        assert len(chat_ids) == 8  # 每个线程新建独立会话
        assert len(_CHAT_SESSIONS) == 8
