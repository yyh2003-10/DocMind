"""流式对话连接鲁棒性与异常恢复测试。

覆盖：
1. incomplete chunked read / peer closed connection 瞬时断连的自动重试与断点续传
2. 多次网络抖动后重试耗尽的平滑降级（Graceful Degradation 保全已生成内容）
3. is_transient_network_error 递归异常链判定
4. 覆盖 OpenAI / Anthropic / Gemini / Ollama 各提供商的断连恢复与平滑收尾
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from doc2mind.core.llm.anthropic_impl import AnthropicClient
from doc2mind.core.llm.base import (
    LLMError,
    is_transient_network_error,
    iter_exception_chain,
)
from doc2mind.core.llm.gemini_impl import GeminiClient
from doc2mind.core.llm.ollama_impl import OllamaClient
from doc2mind.core.llm.openai_impl import OpenAIClient


# --- 1. 异常链与瞬时错误判定测试 ---
def test_is_transient_network_error_direct() -> None:
    assert is_transient_network_error(ConnectionResetError("Connection reset by peer"))
    assert is_transient_network_error(TimeoutError("timed out"))
    assert is_transient_network_error(
        httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body (incomplete chunked read)"
        )
    )
    assert is_transient_network_error(httpx.ReadTimeout("read timeout"))
    assert is_transient_network_error(httpx.ConnectError("failed to connect"))


def test_is_transient_network_error_nested_chain() -> None:
    # 模拟 openai SDK 封装的 APIConnectionError（__cause__ 为 httpx.RemoteProtocolError）
    cause = httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body (incomplete chunked read)"
    )
    wrapper = Exception("Connection error.")
    wrapper.__cause__ = cause

    chain = iter_exception_chain(wrapper)
    assert len(chain) == 2
    assert is_transient_network_error(wrapper)


def test_is_transient_network_error_windows_socket_codes() -> None:
    # Windows WSAECONNRESET (10054) / WSAETIMEDOUT (10060)
    err = OSError(10054, "An existing connection was forcibly closed by the remote host")
    assert is_transient_network_error(err)


# --- 辅助 Mock 类 ---
class _FakeChunk:
    def __init__(self, content: str | None, reasoning: str | None = None) -> None:
        class Delta:
            def __init__(self, c, r):
                self.content = c
                self.reasoning_content = r
        class Choice:
            def __init__(self, d):
                self.delta = d
                self.finish_reason = None
        self.choices = [Choice(Delta(content, reasoning))]


class _FakeStreamResponse:
    def __init__(self, lines: list[str], fail_with: Exception | None = None) -> None:
        self._lines = list(lines)
        self._fail_with = fail_with
        self.status_code = 200
        self.is_success = True

    def __enter__(self) -> _FakeStreamResponse:
        return self

    def __exit__(self, *a: Any) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    def iter_lines(self) -> Iterator[str]:
        yield from self._lines
        if self._fail_with:
            raise self._fail_with


class _FakeHttpClient:
    def __init__(self, factory_or_resp: Any) -> None:
        self._factory_or_resp = factory_or_resp

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(self, *a: Any) -> None:
        pass

    def stream(self, method: str, url: str, **kw: Any) -> Any:
        if callable(self._factory_or_resp):
            return self._factory_or_resp()
        return self._factory_or_resp


# --- 2. OpenAI 流式重试与平滑降级 ---
class TestOpenAIStreamResilience:
    def test_transient_error_retry_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """第一次流式读取抛出 incomplete chunked read，第二次重试成功输出剩余部分。"""
        calls = 0

        class BrokenThenSuccessStream:
            def __iter__(self):
                nonlocal calls
                calls += 1
                if calls == 1:
                    yield _FakeChunk("第一部分内容，")
                    raise httpx.RemoteProtocolError(
                        "peer closed connection without sending complete message body (incomplete chunked read)"
                    )
                else:
                    yield _FakeChunk("第二部分补充内容。")

        class FakeChatCompletions:
            def create(self, **kwargs):
                return BrokenThenSuccessStream()

        class FakeChat:
            completions = FakeChatCompletions()

        class MockOpenAI:
            def __init__(self, **kw):
                self.chat = FakeChat()
            def close(self):
                pass

        monkeypatch.setattr("openai.OpenAI", MockOpenAI)
        client = OpenAIClient(api_key="sk-test", base_url="http://test")

        tokens = list(client.stream_chat([{"role": "user", "content": "测试"}], timeout=5))
        assert "".join(tokens) == "第一部分内容，第二部分补充内容。"
        assert calls == 2

    def test_retry_exhausted_graceful_degradation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """重试耗尽，但已产出足量正文（>= 10 字符），安全平滑收尾，不向上抛错中断。"""
        class AlwaysBrokenStream:
            def __iter__(self):
                yield _FakeChunk("这是一段非常重要的完整输出内容片段。")
                raise httpx.RemoteProtocolError(
                    "peer closed connection without sending complete message body (incomplete chunked read)"
                )

        class FakeChatCompletions:
            def create(self, **kwargs):
                return AlwaysBrokenStream()

        class FakeChat:
            completions = FakeChatCompletions()

        class MockOpenAI:
            def __init__(self, **kw):
                self.chat = FakeChat()
            def close(self):
                pass

        monkeypatch.setattr("openai.OpenAI", MockOpenAI)
        client = OpenAIClient(api_key="sk-test", base_url="http://test")

        tokens = list(client.stream_chat([{"role": "user", "content": "测试"}], timeout=5))
        full_text = "".join(tokens)
        assert len(full_text) >= 10
        assert "这是一段非常重要的完整输出内容片段。" in full_text

    def test_immediate_failure_raises_informative_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """首字未出时连续断开，抛出友好中文提示。"""
        class ImmediateBrokenStream:
            def __iter__(self):
                if False:
                    yield _FakeChunk("never")
                raise httpx.RemoteProtocolError(
                    "peer closed connection without sending complete message body (incomplete chunked read)"
                )

        class FakeChatCompletions:
            def create(self, **kwargs):
                return ImmediateBrokenStream()

        class FakeChat:
            completions = FakeChatCompletions()

        class MockOpenAI:
            def __init__(self, **kw):
                self.chat = FakeChat()
            def close(self):
                pass

        monkeypatch.setattr("openai.OpenAI", MockOpenAI)
        client = OpenAIClient(api_key="sk-test", base_url="http://test")

        with pytest.raises(LLMError, match="网络连接不稳定或代理超时"):
            list(client.stream_chat([{"role": "user", "content": "测试"}], timeout=5))


# --- 3. Anthropic 流式网络容错 ---
class TestAnthropicStreamResilience:
    def test_anthropic_stream_retry_and_smooth_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lines = [
            "data: " + json.dumps({"type": "content_block_delta", "delta": {"text": "Anthropic 生成了一大段完整分析文字。"}}),
        ]
        resp = _FakeStreamResponse(lines, fail_with=httpx.RemoteProtocolError("peer closed connection"))
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: _FakeHttpClient(resp))

        client = AnthropicClient(api_key="ant-key")
        tokens = list(client.stream_chat([{"role": "user", "content": "测试"}], timeout=5))
        assert "Anthropic 生成了一大段完整分析文字。" in "".join(tokens)


# --- 4. Gemini 流式网络容错 ---
class TestGeminiStreamResilience:
    def test_gemini_stream_retry_and_smooth_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lines = [
            "data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": "Gemini 输出了详细解答方案内容。"}]}}]}),
        ]
        resp = _FakeStreamResponse(lines, fail_with=httpx.RemoteProtocolError("peer closed connection without sending complete message body"))
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: _FakeHttpClient(resp))

        client = GeminiClient(api_key="gem-key")
        tokens = list(client.stream_chat([{"role": "user", "content": "测试"}], timeout=5))
        assert "Gemini 输出了详细解答方案内容。" in "".join(tokens)


# --- 5. Ollama 流式网络容错 ---
class TestOllamaStreamResilience:
    def test_ollama_stream_retry_and_smooth_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lines = [
            json.dumps({"message": {"content": "Ollama 本地大模型顺利生成了一大段文本答案。"}}),
        ]
        resp = _FakeStreamResponse(lines, fail_with=httpx.RemoteProtocolError("incomplete chunked read"))
        monkeypatch.setattr(httpx, "Client", lambda timeout=None: _FakeHttpClient(resp))

        client = OllamaClient(model="llama3.2")
        tokens = list(client.stream_chat([{"role": "user", "content": "测试"}], timeout=5))
        assert "Ollama 本地大模型顺利生成了一大段文本答案。" in "".join(tokens)
