"""LLM 客户端抽象基类与异常。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Iterator

# 默认 LLM 调用超时（秒），防 API 挂起阻塞请求线程
DEFAULT_TIMEOUT = 120

# 主流 LLM 网关对输出 token 上限的常见硬限制（sensenova 等严格校验网关
# 实测 [1, 65536]）。超出时选择不传该参数，由服务端取模型默认上限。
MAX_TOKENS_CEILING = 65536


def sanitize_max_tokens(value: int | None) -> int | None:
    """max_tokens 合法性归一：超上限返回 None（不传，由服务端取默认）。

    用户常把「上下文窗口」（如 256000）误当输出上限填进 llm_max_tokens，
    会被严格校验的网关 400 拒绝（field MaxTokens invalid）。返回 None 时
    调用方应省略该参数；对必填该字段的 provider（如 Anthropic），应退回
    一个该 provider 一定接受的安全默认值。
    """
    if value is None:
        return None
    if value < 1:
        return 1
    if value > MAX_TOKENS_CEILING:
        return None
    return value


class LLMError(Exception):
    """LLM 调用异常。"""


class LLMTimeoutError(LLMError):
    """LLM 调用超时。"""


class LLMClient(ABC):
    """大模型客户端抽象基类。

    子类必须实现：
        - `chat(messages, temperature, max_tokens) -> str`
        - `model_name` 属性
        - `provider` 属性（"openai" / "ollama"）
    """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """当前使用的模型名称。"""
        raise NotImplementedError

    @property
    @abstractmethod
    def provider(self) -> str:
        """提供商标识：openai | ollama。"""
        raise NotImplementedError

    @abstractmethod
    def _do_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """子类实现的实际 LLM 调用（非流式）。"""
        raise NotImplementedError

    def _do_stream_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """子类实现的流式 LLM 调用，逐 token 产出。

        默认实现：回退到非流式 _do_chat，把完整回答作为单 token 产出。
        需要真正流式输出的子类应覆盖此方法。
        """
        reply = self._do_chat(messages, temperature, max_tokens)
        yield reply

    def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        """发送对话消息，返回 LLM 回复文本（带超时保护）。

        Args:
            messages: OpenAI 格式消息列表
            temperature: 温度（覆盖默认值）
            max_tokens: 最大 token 数（覆盖默认值）
            timeout: 超时秒数，None 使用 DEFAULT_TIMEOUT

        Returns:
            模型回复文本

        Raises:
            LLMError: API 调用失败
            LLMTimeoutError: 调用超时
        """
        effective_timeout = timeout if timeout and timeout > 0 else DEFAULT_TIMEOUT
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._do_chat, messages, temperature, max_tokens)
            try:
                return future.result(timeout=effective_timeout)
            except FuturesTimeoutError:
                raise LLMTimeoutError(
                    f"LLM 调用超时 ({effective_timeout}s)，请检查网络或增加 DOC2MIND_LLM_TIMEOUT"
                ) from None
            except LLMError:
                raise
            except Exception as e:
                raise LLMError(f"LLM 调用失败: {e}") from e

    def stream_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> Iterator[str]:
        """流式对话，逐 token 产出（带超时保护）。

        Args:
            messages: OpenAI 格式消息列表
            temperature: 温度（覆盖默认值）
            max_tokens: 最大 token 数（覆盖默认值）
            timeout: 超时秒数，None 使用 DEFAULT_TIMEOUT

        Yields:
            逐 token 文本

        Raises:
            LLMError: API 调用失败
            LLMTimeoutError: 调用超时
        """
        effective_timeout = timeout if timeout and timeout > 0 else DEFAULT_TIMEOUT
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                list,
                self._do_stream_chat(messages, temperature, max_tokens),
            )
            try:
                tokens = future.result(timeout=effective_timeout)
                yield from tokens
            except FuturesTimeoutError:
                raise LLMTimeoutError(
                    f"LLM 流式调用超时 ({effective_timeout}s)，请检查网络或增加 DOC2MIND_LLM_TIMEOUT"
                ) from None
            except LLMError:
                raise
            except Exception as e:
                raise LLMError(f"LLM 流式调用失败: {e}") from e
