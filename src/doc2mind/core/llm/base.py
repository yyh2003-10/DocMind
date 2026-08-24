"""LLM 客户端抽象基类与异常。"""

from __future__ import annotations

import queue
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

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

    def list_models(self, timeout: float | None = None) -> list[str]:
        """列出该提供商当前可用的模型 ID（设置页/对话页下拉选择用）。

        默认实现：不支持；子类按各自 API 实现（Ollama /api/tags、
        OpenAI /models、Anthropic /v1/models、Gemini /v1beta/models）。
        """
        raise LLMError(f"提供商 {self.provider} 暂不支持列出模型，请手动输入模型名")

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
        stop_event: Any | None = None,
    ) -> Iterator[str]:
        """流式对话，逐 token 产出（仅正文；推理链见 stream_chat_tagged）。"""
        for kind, text in self.stream_chat_tagged(
            messages, temperature, max_tokens, timeout, stop_event
        ):
            if kind == "content":
                yield text

    def stream_chat_tagged(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        stop_event: Any | None = None,
    ) -> Iterator[tuple[str, str]]:
        """流式对话，产出 (kind, text) 标记帧：kind ∈ {"thinking", "content"}。

        - "thinking"：模型推理链（如 DeepSeek-R1 / Qwen3 的 reasoning_content），
          仅在支持推理的提供商实现中产出；
        - "content"：最终回答正文。

        默认实现把全部输出当作 content；支持推理的提供商会覆写
        `_do_stream_chat_tagged`。带超时保护与取消事件支持。

        Args:
            messages: OpenAI 格式消息列表
            temperature: 温度（覆盖默认值）
            max_tokens: 最大 token 数（覆盖默认值）
            timeout: 超时秒数，None 使用 DEFAULT_TIMEOUT
            stop_event: 外部取消事件（threading.Event），置位时立即终止生成

        Raises:
            LLMError: API 调用失败
            LLMTimeoutError: 调用超时

        实现说明（真流式，勿改回全量缓冲）：
        队列泵：worker 线程逐帧推入队列，主线程逐个取出即 yield——首帧在
        生成器产出第一个帧时立即到达。超时按「整个流必须在 effective_timeout
        内结束」计算，已收发的帧不受影响。
        """
        from queue import Empty as _QueueEmpty

        effective_timeout = timeout if timeout and timeout > 0 else DEFAULT_TIMEOUT
        q: queue.Queue[object] = queue.Queue()
        sentinel = object()

        def _produce() -> None:
            """worker：跑真实流，逐帧入队；异常也经队列送回主线程。"""
            try:
                for item in self._do_stream_chat_tagged(messages, temperature, max_tokens):
                    if stop_event is not None and stop_event.is_set():
                        break
                    q.put(item)
                q.put(sentinel)
            except BaseException as e:  # noqa: BLE001 — 异常交给主线程分类处理
                q.put(e)

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(_produce)
            deadline = time.monotonic() + effective_timeout
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMTimeoutError(
                        f"LLM 流式调用超时 ({effective_timeout}s)，"
                        "请检查网络或增加 DOC2MIND_LLM_TIMEOUT"
                    )
                # 使用较短超时切片以便及时响应 stop_event
                slice_timeout = min(remaining, 0.5) if stop_event is not None else remaining
                try:
                    item = q.get(timeout=slice_timeout)
                except _QueueEmpty:
                    if remaining <= 0:
                        raise LLMTimeoutError(
                            f"LLM 流式调用超时 ({effective_timeout}s)，"
                            "请检查网络或增加 DOC2MIND_LLM_TIMEOUT"
                        ) from None
                    continue
                if item is sentinel:
                    break
                if isinstance(item, BaseException):
                    if isinstance(item, LLMError):
                        raise item
                    raise LLMError(f"LLM 流式调用失败: {item}") from item
                assert isinstance(item, tuple) and len(item) == 2, item
                yield item[0], item[1]

    def _do_stream_chat_tagged(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[tuple[str, str]]:
        """默认 tagged 实现：不区分推理，全部按正文产出。
        支持推理的提供商（如 OpenAI 兼容的 DeepSeek-R1/Qwen3）覆写此方法。"""
        for tok in self._do_stream_chat(messages, temperature, max_tokens):
            yield "content", tok
