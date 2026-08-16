"""Anthropic / Gemini 客户端单元测试（mock httpx，不调真实 API）。

另覆盖：save_settings 不落盘 API Key、/v1/llm/test 端点行为。
"""

from __future__ import annotations

import json

import httpx
import pytest

from doc2mind.core.config import Settings
from doc2mind.core.llm.base import LLMClient, LLMError
from doc2mind.core.llm.anthropic_impl import AnthropicClient, _split_system
from doc2mind.core.llm.gemini_impl import GeminiClient, _to_contents


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

    def test_manual_toml_api_key_still_loadable(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI 用户手动写 config.toml 的 key 仍可读取（只是不再写回）。"""
        from doc2mind.core import config as config_mod

        toml = tmp_path / "config.toml"
        monkeypatch.setattr(config_mod, "config_file_path", lambda: toml)
        toml.write_text(
            "[doc2mind]\nllm_provider = \"openai\"\nllm_api_key = \"sk-manual\"\n",
            encoding="utf-8",
        )
        data = config_mod.load_config_file()
        assert data.get("llm_api_key") == "sk-manual"


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
        tc.app.state.doc2mind.settings.llm_provider = "none"  # type: ignore[attr-defined]
        tc.app.state.doc2mind.settings.llm_api_key = None  # type: ignore[attr-defined]
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
        from doc2mind.server import http as http_mod
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


# --- mock httpx.Client 流式工具 ---
class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.status_code = 200
        self.text = "\n".join(lines)

    @property
    def is_success(self) -> bool:
        return self.status_code < 400

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
