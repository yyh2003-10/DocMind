"""集成测试 — HTTP /v1/chat、MCP chat 工具、并发 session 安全。

全部使用 mock，不依赖真实 LLM 或向量库。
"""

from __future__ import annotations

import json
import threading
from contextlib import ExitStack
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
        assert data["sources"][0]["chunk_id"] is not None  # 引用来源含 chunk_id(可点击定位用)

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
            events = [item.replace("data: ", "") for item in lines if item.startswith("data: ")]
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

    def test_post_config_empty_model_clears_existing(self, config_client) -> None:
        """WPF 清空模型名保存 → 推 "" 应显式清除后端已配置的旧模型（而非保留）。"""
        resp = config_client.post("/v1/config", json={
            "llm_provider": "openai",
            "llm_model": "deepseek-chat",
        })
        assert resp.status_code == 200
        assert resp.json()["llm_model"] == "deepseek-chat"

        resp2 = config_client.post("/v1/config", json={
            "llm_provider": "openai",
            "llm_model": "",
        })
        assert resp2.status_code == 200
        data2 = resp2.json()
        # 清除后 llm_model 为 None（未配置态；get_llm_client 会回退 provider 默认模型）
        assert data2["llm_model"] is None
        s = get_settings()
        assert s.llm_model is None or s.llm_model == ""


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


# ======================================================================
# 6. 对话内按请求切换模型（POST /v1/chat 的 model 字段）
# ======================================================================
class TestChatModelOverride:
    @pytest.fixture()
    def capture_client(self):
        """创建 app，捕获 rag 内传给 get_llm_client 的 Settings。"""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/testclient not installed")

        mock_llm = MockLLMClient("回答。")
        hit = _make_hit()
        captured: list[Settings] = []

        def fake_get_llm_client(s):
            captured.append(s)
            return mock_llm

        stack = ExitStack()
        stack.enter_context(patch("doc2mind.core.rag._open_store", return_value=(MagicMock(), MagicMock())))
        stats = SearchStats(query="q", total_hits=1, elapsed_ms=5, vector_candidates=1, bm25_candidates=1)
        mock_retriever = MagicMock()
        mock_retriever.search.return_value = ([hit], stats)
        stack.enter_context(patch("doc2mind.core.rag.Retriever", return_value=mock_retriever))
        stack.enter_context(patch("doc2mind.core.rag.get_llm_client", fake_get_llm_client))
        try:
            from doc2mind.server.http import create_app
            yield TestClient(create_app()), captured
        finally:
            stack.close()

    def test_model_override_reaches_llm_client(self, capture_client) -> None:
        tc, captured = capture_client
        resp = tc.post("/v1/chat", json={"query": "q", "model": "qwen2.5:7b"})
        assert resp.status_code == 200
        assert resp.json()["model"] == "mock-model"
        assert captured, "get_llm_client 未被调用"
        assert captured[0].llm_model == "qwen2.5:7b"

    def test_no_model_uses_configured(self, capture_client) -> None:
        tc, captured = capture_client
        resp = tc.post("/v1/chat", json={"query": "q"})
        assert resp.status_code == 200
        # 未覆盖：settings.llm_model 保持后端配置值（默认空串）
        assert captured[0].llm_model == get_settings().llm_model

    def test_stream_accepts_model_field(self, capture_client) -> None:
        """流式端点接受 model 字段不 422，并透传到 LLM 客户端构造。"""
        tc, captured = capture_client
        with tc.stream("POST", "/v1/chat/stream", json={"query": "q", "model": "llama3.2"}) as response:
            assert response.status_code == 200
            list(response.iter_lines())
        assert captured and captured[0].llm_model == "llama3.2"


# ======================================================================
# 7. 会话历史持久化端点（GET /v1/chats, GET/DELETE /v1/chats/{id}）
# ======================================================================
class TestChatsEndpoints:
    @pytest.fixture()
    def chats_client(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/testclient not installed")

        _CHAT_SESSIONS.clear()
        mock_llm = MockLLMClient("回答。")
        stack = _mock_rag_patches(mock_llm)
        try:
            from doc2mind.server.http import create_app
            yield TestClient(create_app())
        finally:
            stack.close()
            _CHAT_SESSIONS.clear()

    def test_chat_persists_session_to_db(self, chats_client) -> None:
        tc = chats_client
        resp = tc.post("/v1/chat", json={"query": "DocMind 支持哪些格式？"})
        assert resp.status_code == 200
        chat_id = resp.json()["chat_id"]

        # 列表可见，标题取首条问题
        listing = tc.get("/v1/chats").json()
        assert listing["total"] >= 1
        item = next(c for c in listing["chats"] if c["chat_id"] == chat_id)
        assert item["title"] == "DocMind 支持哪些格式？"
        assert item["message_count"] == 2  # user + assistant

        # 详情含全部消息（user 在前，assistant 在后）
        detail = tc.get(f"/v1/chats/{chat_id}").json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert detail["messages"][0]["content"] == "DocMind 支持哪些格式？"

    def test_session_context_recovers_from_db_after_memory_eviction(self, chats_client) -> None:
        """内存 LRU 清空（模拟后端重启）后，续聊仍能从 DB 恢复上下文。"""
        tc = chats_client
        r1 = tc.post("/v1/chat", json={"query": "第一轮问题"}).json()
        chat_id = r1["chat_id"]

        _CHAT_SESSIONS.clear()  # 模拟重启：内存会话丢失

        r2 = tc.post("/v1/chat", json={"query": "第二轮问题", "chat_id": chat_id})
        assert r2.status_code == 200
        assert r2.json()["chat_id"] == chat_id
        # 内存缓存已从 DB 回填
        assert chat_id in _CHAT_SESSIONS
        assert len(_CHAT_SESSIONS[chat_id]) >= 3  # 第一轮 2 条 + 第二轮 user

    def test_delete_session(self, chats_client) -> None:
        tc = chats_client
        chat_id = tc.post("/v1/chat", json={"query": "将被删除"}).json()["chat_id"]
        assert tc.delete(f"/v1/chats/{chat_id}").status_code == 200

        assert tc.get(f"/v1/chats/{chat_id}").status_code == 404
        assert tc.delete(f"/v1/chats/{chat_id}").status_code == 404
        assert chat_id not in _CHAT_SESSIONS

    def test_get_missing_chat_404(self, chats_client) -> None:
        assert chats_client.get("/v1/chats/no-such-chat").status_code == 404

    def test_empty_listing(self, chats_client) -> None:
        data = chats_client.get("/v1/chats").json()
        assert data["chats"] == []
        assert data["total"] == 0


# ======================================================================
# 8. 系统提示词自定义（rag_system_prompt）
# ======================================================================
class TestRagSystemPrompt:
    @pytest.fixture()
    def config_client(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/testclient not installed")

        original = get_settings()
        set_settings(Settings())
        try:
            with patch("doc2mind.core.config.save_settings"):
                from doc2mind.server.http import create_app
                yield TestClient(create_app())
        finally:
            set_settings(original)

    def test_config_round_trip_and_clear(self, config_client) -> None:
        tc = config_client
        # 默认未配置
        assert tc.get("/v1/config").json()["rag_system_prompt"] is None

        # 设置
        r = tc.post("/v1/config", json={"rag_system_prompt": "你是一个简洁的中文技术文档助手。"})
        assert r.status_code == 200
        assert r.json()["rag_system_prompt"] == "你是一个简洁的中文技术文档助手。"
        assert get_settings().rag_system_prompt == "你是一个简洁的中文技术文档助手。"

        # 空字符串 = 显式清除
        r2 = tc.post("/v1/config", json={"rag_system_prompt": ""})
        assert r2.status_code == 200
        assert r2.json()["rag_system_prompt"] is None
        assert get_settings().rag_system_prompt is None

    def test_custom_prompt_used_in_llm_messages(self) -> None:
        """自定义提示词替换内置 _SYSTEM_PROMPT 进入 LLM 消息列表。"""
        mock_llm = MockLLMClient("回答。")
        s = Settings(llm_provider="openai", llm_api_key="test", rag_system_prompt="用文言文回答。")
        stack = _mock_rag_patches(mock_llm)
        try:
            rag_answer(query="问题", settings=s, llm_client=mock_llm)
        finally:
            stack.close()
        system_msg = mock_llm.last_messages[0]
        assert system_msg["role"] == "system"
        assert system_msg["content"] == "用文言文回答。"

    def test_default_prompt_when_unset(self) -> None:
        mock_llm = MockLLMClient("回答。")
        s = Settings(llm_provider="openai", llm_api_key="test", rag_system_prompt=None)
        stack = _mock_rag_patches(mock_llm)
        try:
            rag_answer(query="问题", settings=s, llm_client=mock_llm)
        finally:
            stack.close()
        assert "DocMind 知识库问答的智能架构师" in mock_llm.last_messages[0]["content"]


class TestGraphEndpoints:
    """知识图谱 HTTP 端点集成测试。"""

    def test_graph_visualize_and_relations(self, tmp_path) -> None:
        from starlette.testclient import TestClient

        from doc2mind.core.store.graph_store import GraphStore
        from doc2mind.server.http import create_app

        db_file = tmp_path / "graph_test.db"
        s = Settings(db_path=db_file)
        store = GraphStore(db_file)
        store.upsert_entity("FastAPI", "tech", "default")
        store.upsert_entity("Python", "tech", "default")
        e1 = store.upsert_entity("FastAPI", "tech", "default")
        e2 = store.upsert_entity("Python", "tech", "default")
        store.upsert_relation(e1, e2, "written_in")

        app = create_app(s)
        client = TestClient(app)

        # GET /v1/graph/visualize
        res = client.get("/v1/graph/visualize?collection=default")
        assert res.status_code == 200
        data = res.json()
        assert data["total_nodes"] == 2
        assert len(data["edges"]) == 1
        assert data["edges"][0]["label"] == "written_in"

        # GET /v1/graph/entities
        res_ent = client.get("/v1/graph/entities")
        assert res_ent.status_code == 200
        assert len(res_ent.json()) == 2

        # GET /v1/graph/relations/{entity_id}
        res_rel = client.get(f"/v1/graph/relations/{e1}")
        assert res_rel.status_code == 200
        assert len(res_rel.json()) == 1
        assert res_rel.json()[0]["relation"] == "written_in"

        # GET /v1/graph/stats
        res_stats = client.get("/v1/graph/stats")
        assert res_stats.status_code == 200
        assert res_stats.json()["entity_count"] == 2
        assert res_stats.json()["relation_count"] == 1

    def test_graph_extract_requires_llm(self, tmp_path) -> None:
        from starlette.testclient import TestClient

        from doc2mind.server.http import create_app

        s = Settings(db_path=tmp_path / "graph_extract_test.db", llm_provider="none")
        app = create_app(s)
        client = TestClient(app)

        res = client.post("/v1/graph/extract?collection=default")
        assert res.status_code == 400
        assert "未配置 LLM" in res.json()["detail"]["message"]


class TestEventsBroadcast:
    """/v1/events SSE 广播测试。"""

    @pytest.mark.xfail(reason="Starlette TestClient 不支持 async SSE generator 的 iter_lines()")
    def test_events_endpoint_ready_frame(self, tmp_path) -> None:
        from starlette.testclient import TestClient

        from doc2mind.server.http import create_app

        s = Settings(db_path=tmp_path / "events_test.db")
        app = create_app(s)
        client = TestClient(app)

        with client.stream("GET", "/v1/events") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: "):])
                    assert payload["type"] == "ready"
                    break

    def test_broadcast_event_helper(self) -> None:
        import asyncio

        from doc2mind.server.http import _SSE_CONNECTIONS, _broadcast_event, _sse_lock

        loop = asyncio.new_event_loop()
        q: asyncio.Queue = asyncio.Queue()
        entry = (loop, q)
        with _sse_lock:
            _SSE_CONNECTIONS.add(entry)

        try:
            _broadcast_event({"type": "file_ingested", "path": "test.md"})
            # 执行事件循环处理投递
            loop.run_until_complete(asyncio.sleep(0.01))
            assert not q.empty()
            item = q.get_nowait()
            data = json.loads(item)
            assert data["type"] == "file_ingested"
            assert data["path"] == "test.md"
        finally:
            with _sse_lock:
                _SSE_CONNECTIONS.discard(entry)
            loop.close()


