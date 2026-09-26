"""Anthropic / Gemini 客户端单元测试（mock httpx，不调真实 API）。

另覆盖：save_settings 不落盘 API Key、/v1/llm/test 与 /v1/llm/models 端点行为。
"""

from __future__ import annotations

import json

import httpx
import pytest

from doc2mind.core.config import Settings
from doc2mind.core.llm.anthropic_impl import AnthropicClient, _split_system
from doc2mind.core.llm.base import LLMClient, LLMError, LLMTimeoutError
from doc2mind.core.llm.gemini_impl import GeminiClient, _to_contents
from doc2mind.core.llm.ollama_impl import OllamaClient


def _resp(status: int = 200, json_data: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=json_data or {}, request=httpx.Request("POST", "http://test"))


# --- Anthropic ---
class TestAnthropicClient:
    def test_split_system(self) -> None:
        system, rest = _split_system([
            {"role": "system", "content": "S1"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "system", "content": "S2"},
        ])
        assert system == "S1\n\nS2"
        assert rest == [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"}]

    def test_chat_parses_content_and_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict = {}

        def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
            calls.update(url=url, json=json, headers=headers)
            return _resp(200, {"content": [{"type": "text", "text": " 你好 "}]})

        monkeypatch.setattr(httpx, "post", fake_post)
        client = AnthropicClient(api_key="sk-ant-x", model="claude-sonnet-4-5")
        assert client.chat([{"role": "user", "content": "hi"}]) == "你好"
        assert calls["url"] == "https://api.anthropic.com/v1/messages"
        assert calls["headers"]["x-api-key"] == "sk-ant-x"
        assert calls["headers"]["anthropic-version"] == "2023-06-01"

    def test_chat_system_prompts_become_top_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict = {}

        def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
            calls.update(json=json)
            return _resp(200, {"content": [{"type": "text", "text": "ok"}]})

        monkeypatch.setattr(httpx, "post", fake_post)
        client = AnthropicClient(api_key="k")
        client.chat([
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "u"},
        ])
        assert calls["json"]["system"] == "SYS"
        assert calls["json"]["messages"] == [{"role": "user", "content": "u"}]

    def test_chat_custom_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
            seen["url"] = url
            return _resp(200, {"content": [{"type": "text", "text": "ok"}]})

        monkeypatch.setattr(httpx, "post", fake_post)
        AnthropicClient(api_key="k", base_url="http://proxy.local/").chat([{"role": "user", "content": "u"}])
        assert seen["url"] == "http://proxy.local/v1/messages"

    def test_401_reports_invalid_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **kw: _resp(401, {"error": {"message": "invalid x-api-key"}}),
        )
        client = AnthropicClient(api_key="bad")
        with pytest.raises(LLMError, match="API Key 无效"):
            client.chat([{"role": "user", "content": "u"}])

    def test_connection_error_reports_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_post(*a, **kw):
            raise httpx.ConnectError("dns fail")

        monkeypatch.setattr(httpx, "post", fake_post)
        client = AnthropicClient(api_key="k", base_url="http://no-such-host")
        with pytest.raises(LLMError, match="无法连接"):
            client.chat([{"role": "user", "content": "u"}])

    def test_stream_parses_sse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lines = [
            "data: " + json.dumps({"type": "message_start"}),
            "data: " + json.dumps({"type": "content_block_delta", "delta": {"text": "你"}}),
            "data: " + json.dumps({"type": "content_block_delta", "delta": {"text": "好"}}),
            "data: " + json.dumps({"type": "message_stop"}),
        ]
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: _FakeHttpClient(_FakeStreamResponse(lines)))
        client = AnthropicClient(api_key="k")
        tokens = list(client.stream_chat([{"role": "user", "content": "hi"}], timeout=5))
        assert tokens == ["你", "好"]

    def test_stream_thinking_delta_tagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """extended thinking：thinking_delta 透出为 thinking 帧，正文不受影响。"""
        lines = [
            "data: " + json.dumps({"type": "content_block_start", "content_block": {"type": "thinking"}}),
            "data: " + json.dumps({"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "先分析需求"}}),
            "data: " + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "答案A"}}),
            "data: " + json.dumps({"type": "message_stop"}),
        ]
        fake = _RecordingStreamClient(_FakeStreamResponse(lines))
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: fake)
        client = AnthropicClient(api_key="k", model="claude-3-7-sonnet")
        tagged = list(client.stream_chat_tagged([{"role": "user", "content": "hi"}], timeout=5))
        assert tagged == [("thinking", "先分析需求"), ("content", "答案A")]
        # 推理模型请求应启用 extended thinking 且不带 temperature
        payload = fake.stream_kwargs["json"]
        assert payload["thinking"]["type"] == "enabled"
        assert 1024 <= payload["thinking"]["budget_tokens"] < payload["max_tokens"]
        assert "temperature" not in payload

    def test_stream_plain_model_payload_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非推理型号不带 thinking 参数，保持旧行为（兼容不支持该特性的网关）。"""
        lines = ["data: " + json.dumps({"type": "message_stop"})]
        fake = _RecordingStreamClient(_FakeStreamResponse(lines))
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: fake)
        client = AnthropicClient(api_key="k", model="claude-sonnet-4-5")
        list(client.stream_chat([{"role": "user", "content": "hi"}], timeout=5))
        assert "thinking" not in fake.stream_kwargs["json"]
        assert fake.stream_kwargs["json"]["temperature"] == 0.7


# --- Gemini ---
class TestGeminiClient:
    def test_to_contents(self) -> None:
        system, contents = _to_contents([
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ])
        assert system == {"parts": [{"text": "SYS"}]}
        assert contents == [
            {"role": "user", "parts": [{"text": "u1"}]},
            {"role": "model", "parts": [{"text": "a1"}]},
        ]

    def test_chat_parses_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict = {}

        def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
            calls.update(url=url, json=json, headers=headers)
            return _resp(200, {"candidates": [{"content": {"parts": [{"text": " hello "}]}}]})

        monkeypatch.setattr(httpx, "post", fake_post)
        client = GeminiClient(api_key="g-key", model="gemini-2.5-flash")
        assert client.chat([{"role": "user", "content": "hi"}]) == "hello"
        assert calls["url"].endswith("/v1beta/models/gemini-2.5-flash:generateContent")
        assert calls["headers"]["x-goog-api-key"] == "g-key"

    def test_chat_blocked_reports_safety(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **kw: _resp(200, {"promptFeedback": {"blockReason": "SAFETY"}}),
        )
        client = GeminiClient(api_key="k")
        with pytest.raises(LLMError, match="安全策略"):
            client.chat([{"role": "user", "content": "u"}])

    def test_401_reports_invalid_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **kw: _resp(401, {"error": {"message": "API key not valid"}}),
        )
        client = GeminiClient(api_key="bad")
        with pytest.raises(LLMError, match="API Key 无效"):
            client.chat([{"role": "user", "content": "u"}])

    def test_stream_parses_sse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lines = [
            "data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": "你"}]}}]}),
            "data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": "好"}]}}]}),
        ]
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: _FakeHttpClient(_FakeStreamResponse(lines)))
        client = GeminiClient(api_key="k")
        tokens = list(client.stream_chat([{"role": "user", "content": "hi"}], timeout=5))
        assert tokens == ["你", "好"]

    def test_stream_thought_parts_tagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """思考摘要：thought part 透出为 thinking 帧，正文走 content 帧。"""
        lines = [
            "data: " + json.dumps({"candidates": [{"content": {"parts": [
                {"text": "先检索知识库", "thought": True},
            ]}}]}),
            "data: " + json.dumps({"candidates": [{"content": {"parts": [
                {"text": "答案B"},
            ]}}]}),
        ]
        fake = _RecordingStreamClient(_FakeStreamResponse(lines))
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: fake)
        client = GeminiClient(api_key="k", model="gemini-2.5-flash")
        tagged = list(client.stream_chat_tagged([{"role": "user", "content": "hi"}], timeout=5))
        assert tagged == [("thinking", "先检索知识库"), ("content", "答案B")]
        # 2.5 系列应请求 includeThoughts 才能收到 thought part
        gen_cfg = fake.stream_kwargs["json"]["generationConfig"]
        assert gen_cfg["thinkingConfig"] == {"includeThoughts": True}
        # 非流式正文不混入思考链（thought part 被 _extract_text 排除）
        non_stream = _resp(200, {"candidates": [{"content": {"parts": [
            {"text": "思考中", "thought": True}, {"text": "正文C"},
        ]}}]})
        monkeypatch.setattr(
            httpx, "post", lambda *a, **kw: non_stream,  # noqa: ARG005
        )
        assert client.chat([{"role": "user", "content": "hi"}]) == "正文C"

    def test_stream_old_model_no_thinking_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不支持思考的旧系列（1.5/2.0）不带 thinkingConfig，避免 API 400。"""
        lines = ["data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})]
        fake = _RecordingStreamClient(_FakeStreamResponse(lines))
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: fake)
        client = GeminiClient(api_key="k", model="gemini-1.5-flash")
        list(client.stream_chat([{"role": "user", "content": "hi"}], timeout=5))
        assert "thinkingConfig" not in fake.stream_kwargs["json"]["generationConfig"]


# --- Ollama 思考链 ---
class TestOllamaThinkingStream:
    def test_r1_think_param_and_frames(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """推理模型：请求带 think=true，思考链走 thinking 帧、正文走 content 帧。"""
        lines = [
            json.dumps({"message": {"thinking": "拆解问题"}, "done": False}),
            json.dumps({"message": {"content": "答案D"}, "done": False}),
            json.dumps({"message": {}, "done": True}),
        ]
        fake = _RecordingStreamClient(_FakeStreamResponse(lines))
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: fake)
        client = OllamaClient(model="deepseek-r1:7b")
        tagged = list(client.stream_chat_tagged([{"role": "user", "content": "hi"}], timeout=5))
        assert tagged == [("thinking", "拆解问题"), ("content", "答案D")]
        assert fake.stream_kwargs["json"]["think"] is True

    def test_plain_model_no_think_param(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非推理模型不带 think 参数（Ollama 会拒绝），行为与旧实现一致。"""
        lines = [json.dumps({"message": {"content": "ok"}, "done": True})]
        fake = _RecordingStreamClient(_FakeStreamResponse(lines))
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: fake)
        client = OllamaClient(model="llama3.2")
        tokens = list(client.stream_chat([{"role": "user", "content": "hi"}], timeout=5))
        assert tokens == ["ok"]
        assert "think" not in fake.stream_kwargs["json"]


# --- save_settings 不落盘 API Key ---
class TestApiKeyNotPersisted:
    def test_save_settings_omits_api_key(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        from doc2mind.core import config as config_mod
        from doc2mind.core.config import save_settings

        toml = tmp_path / "config.toml"
        monkeypatch.setattr(config_mod, "config_file_path", lambda: toml)
        save_settings(Settings(llm_provider="openai", llm_api_key="sk-secret", llm_model="deepseek-chat"))
        content = toml.read_text(encoding="utf-8")
        assert "sk-secret" not in content
        assert "llm_api_key" not in content
        assert 'llm_model = "deepseek-chat"' in content

    def test_manual_toml_api_key_ignored_by_design(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """安全设计：手写 config.toml 的 llm_api_key 一律忽略（不再回读明文密钥）。

        1.1 起为消除 API Key 明文落盘的泄漏面，config.toml 只承载非敏感字段，
        密钥仅由环境变量 DOC2MIND_LLM_API_KEY / POST /v1/config 运行时注入。
        本用例锁定该行为：敏感字段被丢弃，其余字段照常读取且解析无错。
        """
        from doc2mind.core import config as config_mod

        toml = tmp_path / "config.toml"
        monkeypatch.setattr(config_mod, "config_file_path", lambda: toml)
        toml.write_text(
            '[doc2mind]\nllm_provider = "openai"\n'
            'llm_api_key = "sk-manual"\nllm_model = "deepseek-chat"\n',
            encoding="utf-8",
        )
        data = config_mod.load_config_file()
        # 敏感字段被丢弃（不回读）
        assert "llm_api_key" not in data
        # 非敏感字段照常读取
        assert data.get("llm_provider") == "openai"
        assert data.get("llm_model") == "deepseek-chat"
        assert config_mod.get_config_load_error() is None


# --- POST /v1/llm/test 端点 ---
class _OkClient(LLMClient):
    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def provider(self) -> str:
        return "mock"

    def _do_chat(self, messages, temperature=None, max_tokens=None) -> str:  # noqa: ANN001
        return "pong"


class _FailClient(LLMClient):
    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def provider(self) -> str:
        return "mock"

    def _do_chat(self, messages, temperature=None, max_tokens=None) -> str:  # noqa: ANN001
        raise LLMError("mock: 401 API Key 无效")


class TestLlmTestEndpoint:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient

        from doc2mind.server.http import create_app

        tc = TestClient(create_app())
        # 隔离全局配置：不受本机 config.toml / 环境变量影响，避免测试真的调外部 LLM
        # base_url 也必须重置：本机若配了 localhost 地址（如 LM Studio）且服务在跑，
        # 工厂会兜底假 key 并真的调通本地服务，破坏"缺 key 报错"类用例
        tc.app.state.doc2mind.settings.llm_provider = "none"  # type: ignore[attr-defined]
        tc.app.state.doc2mind.settings.llm_api_key = None  # type: ignore[attr-defined]
        tc.app.state.doc2mind.settings.llm_base_url = None  # type: ignore[attr-defined]
        return tc

    def test_none_provider(self, client) -> None:
        resp = client.post("/v1/llm/test", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "未选择" in data["error"]

    def test_invalid_provider(self, client) -> None:
        resp = client.post("/v1/llm/test", json={"provider": "bogus"})
        data = resp.json()
        assert data["ok"] is False
        assert "bogus" in (data["error"] or "")

    def test_success(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        from doc2mind.server import http as http_mod

        monkeypatch.setattr(http_mod, "get_llm_client", lambda s: _OkClient())
        resp = client.post("/v1/llm/test", json={"provider": "ollama"})
        data = resp.json()
        assert data["ok"] is True
        assert data["provider"] == "mock"
        assert data["model"] == "mock-model"
        assert data["reply_preview"] == "pong"

    def test_switching_provider_does_not_reuse_runtime_credentials(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        from doc2mind.server import http as http_mod

        runtime = client.app.state.doc2mind.settings  # type: ignore[attr-defined]
        runtime.llm_provider = "openai"
        runtime.llm_api_key = "old-provider-key"
        runtime.llm_base_url = "https://old.example/v1"
        runtime.llm_model = "old-provider-model"
        captured: list[Settings] = []

        def fake_get(settings):
            captured.append(settings)
            return _OkClient()

        monkeypatch.setattr(http_mod, "get_llm_client", fake_get)
        response = client.post("/v1/llm/test", json={"provider": "ollama"})

        assert response.json()["ok"] is True
        assert captured[0].llm_api_key is None
        assert captured[0].llm_base_url is None
        assert captured[0].llm_model == ""

    def test_llm_error_returns_classified_error(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        from doc2mind.server import http as http_mod

        monkeypatch.setattr(http_mod, "get_llm_client", lambda s: _FailClient())
        resp = client.post("/v1/llm/test", json={"provider": "openai", "api_key": "sk-bad"})
        data = resp.json()
        assert data["ok"] is False
        assert "mock: 401" in (data["error"] or "")

    def test_config_update_rejects_bad_provider(self, client) -> None:
        resp = client.post("/v1/config", json={"llm_provider": "bogus"})
        assert resp.status_code == 400
        assert "bogus" in resp.json()["detail"]["message"]

    def test_config_update_empty_string_clears_key(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        """空字符串 llm_api_key/llm_base_url = 显式清除（null = 不修改）。"""
        from doc2mind.core import config as config_mod

        # 跳过持久化副作用（本测试只关心运行时 settings 被正确清除）
        monkeypatch.setattr(config_mod, "save_settings", lambda s: None)
        app_state = client.app.state.doc2mind  # type: ignore[attr-defined]
        app_state.settings.llm_api_key = "sk-old"
        app_state.settings.llm_base_url = "https://old.example/v1"

        resp = client.post("/v1/config", json={"llm_api_key": "", "llm_base_url": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_api_key_configured"] is False
        assert data["llm_base_url"] is None
        assert app_state.settings.llm_api_key is None
        assert app_state.settings.llm_base_url is None


# --- list_models（设置页/对话页「获取模型列表」） ---
class TestListModels:
    def test_ollama_lists_local_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def fake_get(url, timeout=None):
            seen["url"] = url
            return _resp(200, {"models": [{"name": "llama3.2:latest"}, {"name": "qwen2.5:7b"}, {"name": ""}]})

        monkeypatch.setattr(httpx, "get", fake_get)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)  # 隔离本机 Ollama 环境变量
        client = OllamaClient(model="llama3.2")
        assert client.list_models() == ["llama3.2:latest", "qwen2.5:7b"]
        assert seen["url"] == "http://localhost:11434/api/tags"

    def test_ollama_unreachable_reports_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_get(url, timeout=None):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "get", fake_get)
        client = OllamaClient(model="llama3.2")
        with pytest.raises(LLMError, match="无法连接 Ollama"):
            client.list_models()

    def test_anthropic_lists_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def fake_get(url, headers=None, timeout=None):
            seen.update(url=url, headers=headers)
            return _resp(200, {"data": [{"id": "claude-sonnet-4-5"}, {"id": "claude-3-5-haiku-latest"}]})

        monkeypatch.setattr(httpx, "get", fake_get)
        client = AnthropicClient(api_key="sk-ant-x")
        assert client.list_models() == ["claude-3-5-haiku-latest", "claude-sonnet-4-5"]
        assert seen["url"] == "https://api.anthropic.com/v1/models"
        assert seen["headers"]["x-api-key"] == "sk-ant-x"

    def test_gemini_filters_generate_content_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        def fake_get(url, headers=None, timeout=None):
            seen.update(url=url, headers=headers)
            return _resp(200, {"models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent", "countTokens"]},
            ]})

        monkeypatch.setattr(httpx, "get", fake_get)
        client = GeminiClient(api_key="g-key")
        assert client.list_models() == ["gemini-2.5-flash", "gemini-2.5-pro"]
        assert seen["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models")
        assert seen["headers"]["x-goog-api-key"] == "g-key"

    def test_openai_lists_via_sdk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("openai")
        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")

        class _Model:
            def __init__(self, id: str) -> None:  # noqa: A002
                self.id = id

        class _Page:
            data = [_Model("deepseek-chat"), _Model("deepseek-reasoner")]

        # with_options(...) 返回自身；models.list 返回固定页
        monkeypatch.setattr(type(client._client), "with_options", lambda self, **kw: self, raising=False)
        monkeypatch.setattr(type(client._client.models), "list", lambda *a, **kw: _Page(), raising=False)
        assert client.list_models() == ["deepseek-chat", "deepseek-reasoner"]

    def test_openai_stream_exposes_reasoning_chain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OpenAI 兼容推理模型：reasoning_content 透出为 thinking 帧，正文不受影响。"""
        pytest.importorskip("openai")
        from types import SimpleNamespace

        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")

        def chunk(delta):
            return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

        frames = [
            chunk(SimpleNamespace(reasoning_content="用户想知道B3规格", content=None)),
            chunk(SimpleNamespace(reasoning_content="先查手册再回答", content=None)),
            chunk(SimpleNamespace(reasoning_content="", content="ASDA-B3")),
            chunk(SimpleNamespace(reasoning_content=None, content="最高转速3000rpm")),
        ]
        monkeypatch.setattr(
            type(client._client.chat.completions), "create",
            lambda *a, **kw: frames, raising=False,
        )

        tagged = list(client.stream_chat_tagged([{"role": "user", "content": "q"}], timeout=5))
        assert tagged == [
            ("thinking", "用户想知道B3规格"),
            ("thinking", "先查手册再回答"),
            ("content", "ASDA-B3"),
            ("content", "最高转速3000rpm"),
        ]
        # 兼容入口 stream_chat 只吐正文，行为与旧实现一致
        assert list(client.stream_chat([{"role": "user", "content": "q"}], timeout=5)) == [
            "ASDA-B3", "最高转速3000rpm"
        ]

    def test_openai_stream_resumes_after_midstream_disconnect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """流中途断连：断点续传而非整段重来。

        第一次流产出部分正文后连接被网关掐断（incomplete chunked read）；
        重试时把已产出正文作为 assistant 上下文，模型只补全后半段。
        """
        pytest.importorskip("openai")
        from types import SimpleNamespace

        import httpx

        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")

        def chunk(content: str):
            return SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(reasoning_content=None, content=content),
            )])

        calls: list = []

        def fake_create(*a: object, **kw: object):
            calls.append(kw.get("messages"))
            if len(calls) == 1:
                # 第一次：先产出「前半段」，再在流中途抛瞬时网络错误
                def failing_stream():
                    yield chunk("前半段")
                    raise httpx.RemoteProtocolError(
                        "peer closed connection without sending complete message body "
                        "(incomplete chunked read)"
                    )
                return failing_stream()
            return [chunk("，后半段")]

        monkeypatch.setattr(
            type(client._client.chat.completions), "create", fake_create, raising=False,
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("random.uniform", lambda a, b: 0.0)

        tagged = list(client.stream_chat_tagged([{"role": "user", "content": "q"}], timeout=5))
        # 断点续传：已产出正文 + 补全的后半段，无重复
        assert tagged == [("content", "前半段"), ("content", "，后半段")]
        # 第二次调用携带续传上下文：assistant 已产出正文 + user 续写指令
        assert len(calls) == 2
        resume_msgs = calls[1]
        assert resume_msgs[-2] == {"role": "assistant", "content": "前半段"}
        assert "继续" in resume_msgs[-1]["content"]

    def test_openai_stream_retries_full_when_nothing_emitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未产出任何正文就断连：整段重试（不带续传上下文，避免重复）。"""
        pytest.importorskip("openai")
        from types import SimpleNamespace

        import httpx

        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")

        def chunk(content: str):
            return SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(reasoning_content=None, content=content),
            )])

        calls: list = []

        def fake_create(*a: object, **kw: object):
            calls.append(kw.get("messages"))
            if len(calls) == 1:
                def failing_stream():
                    raise httpx.RemoteProtocolError("connection reset by peer")
                return failing_stream()
            return [chunk("完整回答")]

        monkeypatch.setattr(
            type(client._client.chat.completions), "create", fake_create, raising=False,
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("random.uniform", lambda a, b: 0.0)

        tagged = list(client.stream_chat_tagged([{"role": "user", "content": "q"}], timeout=5))
        assert tagged == [("content", "完整回答")]
        # 未产出正文 → 重试仍用原始消息，不拼接续传上下文
        assert calls[1] == [{"role": "user", "content": "q"}]

    def test_openai_stream_raises_after_retries_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """多次重试仍断连：抛带可行动提示的 LLMError，而非静默丢帧。"""
        pytest.importorskip("openai")
        import httpx

        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")

        def fake_create(*a: object, **kw: object):
            def failing_stream():
                raise httpx.RemoteProtocolError(
                    "peer closed connection without sending complete message body "
                    "(incomplete chunked read)"
                )
            return failing_stream()

        monkeypatch.setattr(
            type(client._client.chat.completions), "create", fake_create, raising=False,
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr("random.uniform", lambda a, b: 0.0)

        with pytest.raises(LLMError) as ei:
            list(client.stream_chat_tagged([{"role": "user", "content": "q"}], timeout=5))
        assert "网络连接不稳定" in str(ei.value)

    def test_base_default_unsupported(self) -> None:
        class _Bare(LLMClient):
            @property
            def model_name(self) -> str:
                return "m"

            @property
            def provider(self) -> str:
                return "mock"

            def _do_chat(self, messages, temperature=None, max_tokens=None) -> str:  # noqa: ANN001
                return ""

        with pytest.raises(LLMError, match="暂不支持列出模型"):
            _Bare().list_models()


class TestStreamIdleTimeout:
    """stream_chat_tagged 空闲超时：持续出帧的长回答不被整体时限误杀。"""

    class _SlowStreamClient(LLMClient):
        """按给定帧间隔慢速出帧的裸客户端（gap 秒/帧）。"""

        def __init__(self, gaps: list[float]) -> None:
            self._gaps = gaps  # 第 i 帧产出前等待的秒数

        @property
        def model_name(self) -> str:
            return "m"

        @property
        def provider(self) -> str:
            return "mock"

        def _do_chat(self, messages, temperature=None, max_tokens=None) -> str:  # noqa: ANN001
            return ""

        def _do_stream_chat_tagged(self, messages, temperature=None, max_tokens=None):  # noqa: ANN001
            import time as _t

            for i, gap in enumerate(self._gaps):
                if gap:
                    _t.sleep(gap)
                yield ("content", f"帧{i}")

    def test_long_stream_with_steady_frames_not_killed(self) -> None:
        """总耗时超过 timeout（~1.8s），但每帧间隔都在时限内（0.6s）
        → 正常完成不抛超时；旧的整体时限实现会在第 2 帧前误杀。"""
        client = self._SlowStreamClient(gaps=[0.6, 0.6, 0.6])
        tagged = list(client.stream_chat_tagged([{"role": "user", "content": "q"}], timeout=1))
        assert tagged == [("content", "帧0"), ("content", "帧1"), ("content", "帧2")]

    def test_stalled_stream_raises_idle_timeout(self) -> None:
        """中途停更且间隔超过 timeout → 抛空闲超时错误（文案可行动）。"""
        client = self._SlowStreamClient(gaps=[0.05, 3.0])
        with pytest.raises(LLMTimeoutError, match="空闲超时"):
            list(client.stream_chat_tagged([{"role": "user", "content": "q"}], timeout=0.5))


# --- POST /v1/llm/models 端点 ---
class _ModelsClient(_OkClient):
    def list_models(self, timeout: float | None = None) -> list[str]:
        return ["m1", "m2"]


class _Models404Client(_OkClient):
    def list_models(self, timeout: float | None = None) -> list[str]:
        raise LLMError("OpenAI API 列出模型失败（模型名或 API 地址不存在）: ... (HTTP 404)")


class TestLlmModelsEndpoint:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient

        from doc2mind.server.http import create_app

        tc = TestClient(create_app())
        # 同 TestLlmTestEndpoint：base_url 一并重置，防止本机 localhost 服务干扰
        tc.app.state.doc2mind.settings.llm_provider = "none"  # type: ignore[attr-defined]
        tc.app.state.doc2mind.settings.llm_api_key = None  # type: ignore[attr-defined]
        tc.app.state.doc2mind.settings.llm_base_url = None  # type: ignore[attr-defined]
        return tc

    def test_none_provider(self, client) -> None:
        resp = client.post("/v1/llm/models", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "未选择" in data["error"]

    def test_invalid_provider(self, client) -> None:
        resp = client.post("/v1/llm/models", json={"provider": "bogus"})
        data = resp.json()
        assert data["ok"] is False
        assert "bogus" in (data["error"] or "")

    def test_success_returns_models(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        from doc2mind.server import http as http_mod

        monkeypatch.setattr(http_mod, "get_llm_client", lambda s: _ModelsClient())
        resp = client.post("/v1/llm/models", json={"provider": "ollama"})
        data = resp.json()
        assert data["ok"] is True
        assert data["models"] == ["m1", "m2"]

    def test_passed_params_override_runtime_config(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        """请求传入的 api_key/base_url 覆盖运行时配置（临时构造，不落盘）。"""
        from doc2mind.server import http as http_mod

        captured: list[Settings] = []

        def fake_get(s):
            captured.append(s)
            return _ModelsClient()

        monkeypatch.setattr(http_mod, "get_llm_client", fake_get)
        resp = client.post("/v1/llm/models", json={
            "provider": "openai",
            "api_key": "sk-ui-input",
            "base_url": "https://api.deepseek.com/v1",
        })
        assert resp.json()["ok"] is True
        assert captured[0].llm_api_key == "sk-ui-input"
        assert captured[0].llm_base_url == "https://api.deepseek.com/v1"
        # 不修改运行时配置
        assert client.app.state.doc2mind.settings.llm_api_key is None  # type: ignore[attr-defined]

    def test_switching_provider_does_not_reuse_runtime_credentials(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        from doc2mind.server import http as http_mod

        runtime = client.app.state.doc2mind.settings  # type: ignore[attr-defined]
        runtime.llm_provider = "openai"
        runtime.llm_api_key = "old-provider-key"
        runtime.llm_base_url = "https://old.example/v1"
        runtime.llm_model = "old-provider-model"
        captured: list[Settings] = []

        def fake_get(settings):
            captured.append(settings)
            return _ModelsClient()

        monkeypatch.setattr(http_mod, "get_llm_client", fake_get)
        response = client.post("/v1/llm/models", json={"provider": "ollama"})

        assert response.json()["ok"] is True
        assert captured[0].llm_api_key is None
        assert captured[0].llm_base_url is None
        assert captured[0].llm_model == ""

    def test_404_appends_manual_input_hint(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        from doc2mind.server import http as http_mod

        monkeypatch.setattr(http_mod, "get_llm_client", lambda s: _Models404Client())
        resp = client.post("/v1/llm/models", json={"provider": "openai", "api_key": "sk-x"})
        data = resp.json()
        assert data["ok"] is False
        assert "手动输入" in (data["error"] or "")

    def test_missing_api_key_classified_error(self, client) -> None:
        """openai 无 key → 工厂抛 LLMError → ok=False（不 500）。"""
        resp = client.post("/v1/llm/models", json={"provider": "openai", "api_key": ""})
        data = resp.json()
        assert data["ok"] is False
        assert "llm_api_key" in (data["error"] or "")


# --- mock httpx.Client 流式工具 ---
class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.status_code = 200
        self.text = "\n".join(lines)

    @property
    def is_success(self) -> bool:
        return self.status_code < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://test"),
                response=httpx.Response(self.status_code),
            )

    def iter_lines(self):
        return iter(self._lines)


class _FakeClientCM:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._r = response

    def __enter__(self) -> _FakeStreamResponse:
        return self._r

    def __exit__(self, *args) -> None:
        return None


class _FakeHttpClient:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._r = response

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(self, *args) -> None:
        return None

    def stream(self, method: str, url: str, **kw):  # noqa: ANN003, ARG002
        return _FakeClientCM(self._r)


class _RecordingStreamClient(_FakeHttpClient):
    """记录 stream() 调用参数的假 httpx.Client（验证请求体门控逻辑用）。"""

    def __init__(self, response: _FakeStreamResponse) -> None:
        super().__init__(response)
        self.stream_kwargs: dict = {}

    def stream(self, method: str, url: str, **kw):  # noqa: ANN003, ARG002
        self.stream_kwargs.update(kw)
        return _FakeClientCM(self._r)


class TestOpenAIClientStreamResilience:
    def test_smooth_finish_on_peer_closed_connection_without_name_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        from doc2mind.core.llm.openai_impl import OpenAIClient

        class FakeChunk:
            def __init__(self, content: str | None, reasoning: str | None = None) -> None:
                class Delta:
                    def __init__(self, c, r):
                        self.content = c
                        self.reasoning_content = r
                class Choice:
                    def __init__(self, d):
                        self.delta = d
                self.choices = [Choice(Delta(content, reasoning))]

        class BrokenStream:
            def __iter__(self):
                # 产生超过 50 个字符的正文
                for token in ["你好，", "这是针对工业动平衡与伺服调试的完整解答指南。", "包含参数设置和故障排查要点："]:
                    yield FakeChunk(token)
                # 随后模拟对端提前关闭连接
                raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")

        class FakeChatCompletions:
            def create(self, **kwargs):
                return BrokenStream()

        class FakeChat:
            completions = FakeChatCompletions()

        class MockOpenAI:
            def __init__(self, **kw):
                self.chat = FakeChat()

            def close(self):
                pass

        monkeypatch.setattr("openai.OpenAI", MockOpenAI)
        client = OpenAIClient(api_key="sk-test", base_url="http://test")

        # 验证 stream_chat 正常产出内容且在对端关闭后平滑收尾，不抛出 NameError 或 LLMError
        tokens = list(client.stream_chat([{"role": "user", "content": "hi"}]))
        full_text = "".join(tokens)
        assert len(full_text) >= 30
        assert "工业动平衡" in full_text


# ---------- model_registry 启发式识别 ----------


def test_model_registry_heuristic_detects_qwen3_reasoning() -> None:
    """qwen3 / qwen3-vl 等 Qwen3 系模型应被启发式识别为推理模型（发送 think: true）。"""
    from doc2mind.core.llm.model_registry import get_model_spec

    reasoning_models = ["qwen3-vl:4b", "qwen3:8b", "qwen3:latest"]
    for m in reasoning_models:
        assert get_model_spec(m, "ollama").is_reasoning_model, f"{m} should be reasoning"

    non_reasoning = ["gemma3:1b", "qwen2.5:7b", "llama3.2:latest"]
    for m in non_reasoning:
        assert not get_model_spec(m, "ollama").is_reasoning_model, f"{m} should NOT be reasoning"


def test_model_registry_heuristic_covers_o4_and_gemini_25() -> None:
    """o4-mini 等 OpenAI o4 系 + Gemini 2.5 系应被正确识别为推理模型。"""
    from doc2mind.core.llm.model_registry import get_model_spec

    reasoning = ["o4-mini", "o3-pro", "gemini-2.5-flash", "gemini-2.5-pro"]
    for m in reasoning:
        spec = get_model_spec(m, "")
        assert spec.is_reasoning_model, f"{m} should be reasoning"

    non_reasoning = ["gemini-2.0-flash", "gemini-1.5-pro", "gpt-4o"]
    for m in non_reasoning:
        spec = get_model_spec(m, "")
        assert not spec.is_reasoning_model, f"{m} should NOT be reasoning"



# ---------- _wrap_api_error 404 提示区分 ----------


class TestWrapApiError404Hint:
    """404 提示需区分「模型已下架（NVIDIA NIM 表现）」与「地址/模型名写错」。"""

    class _ApiErr(Exception):
        status_code = 404

    def test_nvidia_function_gone_hints_model_deprecated(self) -> None:
        """NVIDIA NIM 下架模型：/models 仍列出，调用即 404 Function Not found for account。"""
        pytest.importorskip("openai")
        from doc2mind.core.llm.openai_impl import OpenAIClient

        e = self._ApiErr(
            "Error code: 404 - {'detail': \"Function '23bd454d': "
            "Not found for account 'acct'\"}"
        )
        msg = str(OpenAIClient._wrap_api_error(e, "流式调用"))
        assert "模型已下架或当前账号无权调用" in msg
        assert "更换模型" in msg

    def test_generic_404_keeps_base_url_hint(self) -> None:
        pytest.importorskip("openai")
        from doc2mind.core.llm.openai_impl import OpenAIClient

        msg = str(OpenAIClient._wrap_api_error(self._ApiErr("not found"), "流式调用"))
        assert "模型名或 API 地址不存在" in msg
        assert "base_url 需含 /v1" in msg

# --- 截断信号读取（tasks T5 / 验收 A8） ---
class TestTruncationSignal:
    """四 provider 读取 finish_reason 等价截断信号并暴露 last_truncated。"""

    def test_initial_state_not_truncated(self) -> None:
        pytest.importorskip("openai")
        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")
        assert client.last_truncated is False

    def test_openai_nonstream_length_sets_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("openai")
        from types import SimpleNamespace

        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")
        monkeypatch.setattr(
            type(client._client.chat.completions), "create",
            lambda *a, **kw: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="被截断的回答"),
                finish_reason="length",
            )]),
            raising=False,
        )
        assert client.chat([{"role": "user", "content": "q"}]) == "被截断的回答"
        assert client.last_truncated is True

    def test_openai_nonstream_stop_clears_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("openai")
        from types import SimpleNamespace

        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")
        monkeypatch.setattr(
            type(client._client.chat.completions), "create",
            lambda *a, **kw: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="完整回答"),
                finish_reason="stop",
            )]),
            raising=False,
        )
        client.chat([{"role": "user", "content": "q"}])
        assert client.last_truncated is False

    def test_openai_nonstream_missing_finish_no_false_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("openai")
        from types import SimpleNamespace

        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")
        monkeypatch.setattr(
            type(client._client.chat.completions), "create",
            lambda *a, **kw: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="回答"),
                finish_reason=None,
            )]),
            raising=False,
        )
        client.chat([{"role": "user", "content": "q"}])
        assert client.last_truncated is False

    def test_openai_stream_length_exhausted_sets_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """续写 3 次仍 length → last_truncated=True（T6.1 触发条件）。"""
        pytest.importorskip("openai")
        from types import SimpleNamespace

        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")

        def len_frame():
            return SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(reasoning_content=None, content=None),
                finish_reason="length",
            )])

        monkeypatch.setattr(
            type(client._client.chat.completions), "create",
            lambda *a, **kw: [len_frame()],
            raising=False,
        )
        list(client.stream_chat_tagged([{"role": "user", "content": "q"}], timeout=5))
        assert client.last_truncated is True

    def test_openai_stream_normal_stop_not_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("openai")
        from types import SimpleNamespace

        from doc2mind.core.llm.openai_impl import OpenAIClient

        client = OpenAIClient(api_key="sk-test")

        def stop_frame(content):
            return SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(reasoning_content=None, content=content),
                finish_reason=None,
            )])

        final = SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(reasoning_content=None, content=None),
            finish_reason="stop",
        )])

        monkeypatch.setattr(
            type(client._client.chat.completions), "create",
            lambda *a, **kw: [stop_frame("完整回答"), final],
            raising=False,
        )
        list(client.stream_chat_tagged([{"role": "user", "content": "q"}], timeout=5))
        assert client.last_truncated is False

    def test_anthropic_nonstream_max_tokens_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **kw: _resp(200, {
                "content": [{"type": "text", "text": "被截断"}],
                "stop_reason": "max_tokens",
            }),
        )
        client = AnthropicClient(api_key="k")
        assert client.chat([{"role": "user", "content": "q"}]) == "被截断"
        assert client.last_truncated is True

    def test_anthropic_nonstream_end_turn_not_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **kw: _resp(200, {
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
            }),
        )
        client = AnthropicClient(api_key="k")
        client.chat([{"role": "user", "content": "q"}])
        assert client.last_truncated is False

    def test_gemini_nonstream_max_tokens_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **kw: _resp(200, {
                "candidates": [{
                    "content": {"parts": [{"text": "被截断"}]},
                    "finishReason": "MAX_TOKENS",
                }],
            }),
        )
        client = GeminiClient(api_key="k")
        assert client.chat([{"role": "user", "content": "q"}]) == "被截断"
        assert client.last_truncated is True

    def test_gemini_nonstream_stop_not_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **kw: _resp(200, {
                "candidates": [{
                    "content": {"parts": [{"text": "ok"}]},
                    "finishReason": "STOP",
                }],
            }),
        )
        client = GeminiClient(api_key="k")
        client.chat([{"role": "user", "content": "q"}])
        assert client.last_truncated is False

    def test_ollama_nonstream_length_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **kw: _resp(200, {
                "message": {"content": "被截断"},
                "done_reason": "length",
                "done": True,
            }),
        )
        client = OllamaClient(model="qwen2.5-7b")
        assert client.chat([{"role": "user", "content": "q"}]) == "被截断"
        assert client.last_truncated is True

    def test_ollama_nonstream_stop_not_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx, "post",
            lambda *a, **kw: _resp(200, {
                "message": {"content": "ok"},
                "done_reason": "stop",
                "done": True,
            }),
        )
        client = OllamaClient(model="qwen2.5-7b")
        client.chat([{"role": "user", "content": "q"}])
        assert client.last_truncated is False
