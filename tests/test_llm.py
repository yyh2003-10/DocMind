"""LLM 客户端单元测试（全 mock，不调真实 API）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from doc2mind.core.config import Settings
from doc2mind.core.llm.base import LLMClient, LLMError, LLMTimeoutError
from doc2mind.core.llm.factory import get_llm_client
from doc2mind.core.llm.ollama_impl import OllamaClient
from doc2mind.core.llm.openai_impl import OpenAIClient


# --- Mock LLM Client ---
class MockLLMClient(LLMClient):
    """测试用 mock 客户端，固定返回。"""

    def __init__(self, reply: str = "mock reply") -> None:
        self._reply = reply
        self.last_messages: list[dict] = []

    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def provider(self) -> str:
        return "mock"

    def _do_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.last_messages = messages
        return self._reply


class SlowLLMClient(LLMClient):
    """模拟慢速 LLM（用于超时测试）。"""

    def __init__(self, delay: float = 10.0) -> None:
        self._delay = delay

    @property
    def model_name(self) -> str:
        return "slow-model"

    @property
    def provider(self) -> str:
        return "mock"

    def _do_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        import time
        time.sleep(self._delay)
        return "should not reach"


class GatedStreamLLMClient(LLMClient):
    """流式测试桩：产出第一个 token 后阻塞一小段时间再产出第二个。

    用于验证 stream_chat 是「真流式」——若实现把生成器整体 list() 缓冲，
    拿第一个 token 必须等阻塞结束（耗时 ≈ 总生成时间），首 token 延迟被
    人为拉满；真流式下第一个 token 应立即到达。
    """

    def __init__(self, gate_sleep: float = 0.5) -> None:
        self._gate_sleep = gate_sleep

    @property
    def model_name(self) -> str:
        return "gated-model"

    @property
    def provider(self) -> str:
        return "mock"

    def _do_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        return "first second"

    def _do_stream_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        import time

        yield "first"
        time.sleep(self._gate_sleep)
        yield "second"


# --- Tests: LLMClient 基类 ---
class TestLLMClient:
    def test_abstract_cannot_instantiate(self) -> None:
        """不能直接实例化抽象基类。"""
        with pytest.raises(TypeError):
            LLMClient()  # type: ignore[abstract]

    def test_mock_client_returns_reply(self) -> None:
        client = MockLLMClient("hello")
        result = client.chat([{"role": "user", "content": "hi"}])
        assert result == "hello"
        assert client.last_messages[0]["content"] == "hi"

    def test_stream_chat_default_fallback(self) -> None:
        """默认 _do_stream_chat 回退到 _do_chat，产出完整回复。"""
        client = MockLLMClient("stream test")
        tokens = list(client.stream_chat([{"role": "user", "content": "q"}]))
        assert len(tokens) == 1
        assert tokens[0] == "stream test"

    def test_stream_chat_yields_first_token_immediately(self) -> None:
        """真流式：第一个 token 在完整生成结束前即产出（防止 list() 全量缓冲回归）。

        若实现把生成器整体收集（executor.submit(list, gen)），首 token 必须等
        gate 阻塞（0.5s）结束才到达；真流式下应立即到达。
        """
        import time

        client = GatedStreamLLMClient(gate_sleep=0.5)
        gen = client.stream_chat([{"role": "user", "content": "q"}], timeout=5.0)

        t0 = time.monotonic()
        first = next(gen)
        elapsed_first = time.monotonic() - t0

        assert first == "first"
        assert elapsed_first < 0.3, (
            f"首 token 延迟 {elapsed_first:.3f}s ≥ 0.3s：stream_chat 可能整体缓冲了生成器"
        )

        rest = list(gen)
        assert rest == ["second"]

    def test_stream_chat_preserves_tokens_and_order(self) -> None:
        """逐 token 产出且顺序正确、无丢帧。"""
        client = GatedStreamLLMClient(gate_sleep=0.0)
        tokens = list(client.stream_chat([{"role": "user", "content": "q"}], timeout=5.0))
        assert tokens == ["first", "second"]

    def test_stream_chat_timeout_raises(self) -> None:
        """流式调用整体超时应抛出 LLMTimeoutError。"""
        client = SlowLLMClient(delay=10.0)
        with pytest.raises(LLMTimeoutError, match="超时"):
            list(client.stream_chat(
                [{"role": "user", "content": "hi"}],
                timeout=0.1,
            ))

    def test_chat_timeout_raises(self) -> None:
        """LLM 调用超时应抛出 LLMTimeoutError。"""
        client = SlowLLMClient(delay=10.0)
        with pytest.raises(LLMTimeoutError, match="超时"):
            client.chat(
                [{"role": "user", "content": "hi"}],
                timeout=0.1,
            )

    def test_chat_custom_timeout(self) -> None:
        """自定义 timeout 参数覆盖默认值。"""
        client = MockLLMClient("fast")
        result = client.chat(
            [{"role": "user", "content": "hi"}],
            timeout=5,
        )
        assert result == "fast"


# --- Tests: LLMError / LLMTimeoutError ---
class TestLLMError:
    def test_message(self) -> None:
        err = LLMError("test error")
        assert str(err) == "test error"

    def test_timeout_is_subclass(self) -> None:
        assert issubclass(LLMTimeoutError, LLMError)


# --- Tests: OpenAIClient ---
class TestOpenAIClient:
    def test_init_requires_openai_sdk(self) -> None:
        """验证构造时需要 openai SDK（已安装则不抛异常）。"""
        try:
            client = OpenAIClient(api_key="test-key", model="test-model")
            assert client.model_name == "test-model"
            assert client.provider == "openai"
        except ImportError:
            pytest.skip("openai SDK not installed")

    def test_init_with_base_url(self) -> None:
        try:
            client = OpenAIClient(
                api_key="test-key",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
            )
            assert client.model_name == "deepseek-chat"
        except ImportError:
            pytest.skip("openai SDK not installed")


# --- Tests: OllamaClient ---
class TestOllamaClient:
    def test_defaults(self) -> None:
        client = OllamaClient()
        assert client.model_name == "llama3.2"
        assert client.provider == "ollama"

    def test_custom_model(self) -> None:
        client = OllamaClient(model="qwen2.5", host="http://localhost:11434")
        assert client.model_name == "qwen2.5"

    def test_host_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://custom-host:11434")
        client = OllamaClient()
        assert client._host == "http://custom-host:11434"


# --- Tests: get_llm_client factory ---
class TestGetLLMClient:
    def test_provider_none_returns_none(self) -> None:
        s = Settings(llm_provider="none")
        assert get_llm_client(s) is None

    def test_provider_openai_without_key_raises(self) -> None:
        """配置了 provider 但缺 key → 抛错（而非静默返回 None 让对话时才失败）。"""
        s = Settings(llm_provider="openai", llm_api_key=None)
        with pytest.raises(LLMError, match="llm_api_key"):
            get_llm_client(s)

    def test_provider_openai_with_key(self) -> None:
        try:
            s = Settings(
                llm_provider="openai",
                llm_api_key="sk-test",
                llm_model="deepseek-chat",
            )
            client = get_llm_client(s)
            assert client is not None
            assert isinstance(client, OpenAIClient)
        except ImportError:
            pytest.skip("openai SDK not installed")

    def test_provider_ollama_returns_client(self) -> None:
        s = Settings(llm_provider="ollama", llm_model="llama3.2")
        client = get_llm_client(s)
        assert client is not None
        assert isinstance(client, OllamaClient)
        assert client.model_name == "llama3.2"

    def test_provider_anthropic_returns_client(self) -> None:
        from doc2mind.core.llm.anthropic_impl import AnthropicClient

        s = Settings(llm_provider="anthropic", llm_api_key="sk-ant-test")
        client = get_llm_client(s)
        assert isinstance(client, AnthropicClient)
        assert client.provider == "anthropic"
        assert client.model_name == "claude-sonnet-4-5"  # 默认模型

    def test_provider_anthropic_without_key_raises(self) -> None:
        s = Settings(llm_provider="anthropic", llm_api_key=None)
        with pytest.raises(LLMError, match="llm_api_key"):
            get_llm_client(s)

    def test_provider_gemini_returns_client(self) -> None:
        from doc2mind.core.llm.gemini_impl import GeminiClient

        s = Settings(llm_provider="gemini", llm_api_key="g-test")
        client = get_llm_client(s)
        assert isinstance(client, GeminiClient)
        assert client.provider == "gemini"
        assert client.model_name == "gemini-2.5-flash"  # 默认模型

    def test_provider_gemini_without_key_raises(self) -> None:
        s = Settings(llm_provider="gemini", llm_api_key=None)
        with pytest.raises(LLMError, match="llm_api_key"):
            get_llm_client(s)

    def test_provider_invalid_raises(self) -> None:
        """未知 provider → 抛错并提示可选值（而非静默返回 None）。"""
        s = Settings(llm_provider="invalid_provider")
        with pytest.raises(LLMError, match="invalid_provider"):
            get_llm_client(s)

    def test_timeout_passed_to_client(self) -> None:
        """settings.llm_timeout 应传递到客户端实现（不再用硬编码 120s）。"""
        s = Settings(llm_provider="ollama", llm_timeout=33.0)
        client = get_llm_client(s)
        assert isinstance(client, OllamaClient)
        assert client._timeout == 33.0
