"""LLM 客户端抽象基类与异常。"""

from __future__ import annotations

import logging
import queue
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doc2mind.core.llm.metadata import ModelMetadata

logger = logging.getLogger(__name__)

# 默认 LLM 调用超时（秒），防 API 挂起阻塞请求线程。
# 120s 对 NIM/大 MoE + 慢链路偏紧（TTFT/帧间隔易顶满），默认提到 180s；
# 仍不够时用 DOC2MIND_LLM_TIMEOUT / config.llm_timeout 再调大。
DEFAULT_TIMEOUT = 180

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


def sanitize_max_tokens_v2(
    value: int | None,
    metadata: ModelMetadata | None = None,
) -> int | None:
    """max_tokens 归一 v2（T5 方案 A：天花板随 metadata 声明动态放行）。

    规则：
      - value < 1 → 1
      - value > MAX_TOKENS_CEILING(65536)：
          - 若 metadata 明确声明 max_output_tokens 且 value ≤ 声明值 → 放行（采用 value），留日志
          - 否则 → None（不传，由服务端取默认）
      - 其余 → value

    metadata 为 None 时行为退化为原 `sanitize_max_tokens`。
    """
    if value is None:
        return None
    if value < 1:
        return 1
    if value > MAX_TOKENS_CEILING:
        declared = getattr(metadata, "max_output_tokens", None)
        if isinstance(declared, int) and declared > 0 and value <= declared:
            logger.info(
                "sanitize_max_tokens_v2: max_tokens=%d 超天花板 %d，"
                "但模型声明输出上限 %d，动态放行",
                value,
                MAX_TOKENS_CEILING,
                declared,
            )
            return value
        return None
    return value


def iter_exception_chain(exc: BaseException | None) -> list[BaseException]:
    """展开异常链（__cause__ / __context__），避免循环引用。"""
    chain: list[BaseException] = []
    seen: set[int] = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        chain.append(cur)
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return chain


def merge_stream_retry_text(existing: str, retry_text: str, max_overlap: int = 512) -> str:
    """合并断线续写文本，去掉 retry_text 开头与已有正文的最大重叠部分。"""
    if not retry_text:
        return ""
    if not existing:
        return retry_text
    candidate = retry_text[: max(0, max_overlap)]
    max_len = min(len(existing), len(candidate))
    for size in range(max_len, 0, -1):
        if existing[-size:] == candidate[:size]:
            return retry_text[size:]
    return retry_text


def is_transient_network_error(exc: BaseException | None) -> bool:
    """判断异常是否为可重试的瞬时网络/协议错误（连接重置、断流、超时、EOF 等）。

    递归检查整个异常链（包括底层 httpx / httpcore / socket / OS 错误），
    精准识别 peer closed connection、incomplete chunked read 等代理与网关断流。
    """
    if exc is None:
        return False

    chain = iter_exception_chain(exc)

    # 1. 尝试按具体异常类型判断
    # 标准库 socket / 连接错误
    std_network_types: tuple[type[BaseException], ...] = (
        ConnectionError,
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError,
        TimeoutError,
    )
    for e in chain:
        if isinstance(e, std_network_types):
            return True

    # httpx 异常
    try:
        import httpx

        httpx_types: tuple[type[BaseException], ...] = (
            httpx.TransportError,
            httpx.TimeoutException,
        )
        for e in chain:
            if isinstance(e, httpx_types):
                return True
    except ImportError:
        pass

    # httpcore 异常
    try:
        import httpcore

        httpcore_types = tuple(
            t for t in (
                getattr(httpcore, "NetworkError", None),
                getattr(httpcore, "ProtocolError", None),
                getattr(httpcore, "RemoteProtocolError", None),
                getattr(httpcore, "ReadError", None),
                getattr(httpcore, "WriteError", None),
                getattr(httpcore, "ConnectError", None),
                getattr(httpcore, "PoolTimeout", None),
                getattr(httpcore, "ReadTimeout", None),
                getattr(httpcore, "WriteTimeout", None),
                getattr(httpcore, "ConnectTimeout", None),
            ) if t is not None
        )
        if httpcore_types:
            for e in chain:
                if isinstance(e, httpcore_types):
                    return True
    except ImportError:
        pass

    # openai SDK 异常
    try:
        from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

        openai_types: tuple[type[BaseException], ...] = (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
        for e in chain:
            if isinstance(e, openai_types):
                return True
    except ImportError:
        pass

    # 2. 文本与类名关键字全景匹配（覆盖 Windows Socket 错误码与各类代理网关特征）
    keywords = (
        "peer closed connection",
        "incomplete chunked read",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "chunked encoding",
        "remote protocol",
        "unexpected eof",
        "stream closed",
        "connection closed",
        "connection error",
        "timed out",
        "timeout",
        "unexpected eof",
        "closed prematurely",
        "closed prematurely",
        "server disconnected",
        "forcibly closed by the remote host",
        "remoteprotocolerror",
        "transport error",
        "transport dropped",
        "10054",  # WSAECONNRESET
        "10053",  # WSAECONNABORTED
        "10060",  # WSAETIMEDOUT
        "10061",  # WSAECONNREFUSED
    )

    for e in chain:
        type_name = type(e).__name__.lower()
        if any(k in type_name for k in ("connection", "timeout", "network", "protocol", "transport", "stream")):
            return True
        msg = str(e).lower()
        if any(k in msg for k in keywords):
            return True

    return False


def is_provider_overloaded_error(exc: BaseException | None) -> bool:
    """判断是否为上游过载/限流类错误（NVIDIA NIM / OpenAI 等）。

    典型文案：`Service temporarily overloaded`、`503 Service Unavailable`、
    `The server had an error`。这类错误与网络抖动不同——需要稍长退避后重试，
    不是裁剪上下文能解决的。
    """
    if exc is None:
        return False
    for e in iter_exception_chain(exc):
        status = getattr(e, "status_code", None)
        if status in (429, 502, 503, 529):
            return True
        msg = str(e).lower()
        if any(
            k in msg
            for k in (
                "temporarily overloaded",
                "service overloaded",
                "overloaded",
                "capacity",
                "too many requests",
                "rate limit",
                "server had an error",
                "503 service unavailable",
            )
        ):
            return True
    return False


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
        stop_event: Any | None = None,
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

    @property
    def last_truncated(self) -> bool:
        """最近一次调用是否因输出上限被截断（finish_reason=length 等价信号）。

        provider 在 _do_chat / 流式续写末尾设置 _last_truncated；
        rag 调用层据此发 SSE 截断提示帧（T6.1）与非流式截断日志（T6.2）。
        未设置的 provider 默认 False（不误报）。
        """
        return bool(getattr(self, "_last_truncated", False))

    @last_truncated.setter
    def last_truncated(self, value: bool) -> None:
        self._last_truncated = bool(value)

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
        `_do_stream_chat_tagged`。带空闲超时保护与取消事件支持。

        Args:
            messages: OpenAI 格式消息列表
            temperature: 温度（覆盖默认值）
            max_tokens: 最大 token 数（覆盖默认值）
            timeout: 空闲超时秒数，None 使用 DEFAULT_TIMEOUT
            stop_event: 外部取消事件（threading.Event），置位时立即终止生成

        Raises:
            LLMError: API 调用失败
            LLMTimeoutError: 调用超时

        实现说明（真流式，勿改回全量缓冲）：
        队列泵：worker 线程逐帧推入队列，主线程逐个取出即 yield——首帧在
        生成器产出第一个帧时立即到达。

        超时按「帧间隔空闲超时」计算：任意两帧之间超过 effective_timeout
        未收到新数据才判定挂起；只要持续出帧，长回答不会被整体时限误杀。
        首 token 前的等待同样受此约束（检索/思考期超过时限仍会超时）。
        """
        from queue import Empty as _QueueEmpty

        effective_timeout = timeout if timeout and timeout > 0 else DEFAULT_TIMEOUT
        q: queue.Queue[object] = queue.Queue()
        sentinel = object()

        def _produce() -> None:
            """worker：跑真实流，逐帧入队；异常也经队列送回主线程。"""
            try:
                stream_method = self._do_stream_chat_tagged
                # 兼容第三方/测试子类仍使用旧的三参数 protected hook。
                import inspect

                if "stop_event" in inspect.signature(stream_method).parameters:
                    stream = stream_method(messages, temperature, max_tokens, stop_event)
                else:
                    stream = stream_method(messages, temperature, max_tokens)
                for item in stream:
                    if stop_event is not None and stop_event.is_set():
                        break
                    q.put(item)
                q.put(sentinel)
            except BaseException as e:  # noqa: BLE001 — 异常交给主线程分类处理
                q.put(e)

        # 不使用 `with ThreadPoolExecutor(...)`：其退出时 shutdown(wait=True)，
        # 一旦生产者线程卡在网络读上，消费循环会永久阻塞、流无法结束（断连/超时
        # 场景下前端只能干等）。改为显式 shutdown(wait=False) 并在退出前置位
        # stop_event，让生产者尽快自行退出。
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            executor.submit(_produce)
            idle_deadline = time.monotonic() + effective_timeout
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                remaining = idle_deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMTimeoutError(
                        f"LLM 流式调用空闲超时（{effective_timeout:.0f}s 内无新数据），"
                        "请检查网络或增加 DOC2MIND_LLM_TIMEOUT"
                    )
                # 使用较短超时切片以便及时响应 stop_event；空转后回到循环顶部重新计算截止时间
                slice_timeout = min(remaining, 0.5) if stop_event is not None else remaining
                try:
                    item = q.get(timeout=slice_timeout)
                except _QueueEmpty:
                    continue
                if item is sentinel:
                    break
                if isinstance(item, BaseException):
                    if isinstance(item, LLMError):
                        raise item
                    raise LLMError(f"LLM 流式调用失败: {item}") from item
                assert isinstance(item, tuple) and len(item) == 2, item
                yield item[0], item[1]
                # 收到新帧 → 重置空闲计时：持续出帧的长回答不受整体时限约束
                idle_deadline = time.monotonic() + effective_timeout
        except BaseException:
            # 取消 / 超时 / 异常路径：置位 stop_event 让仍卡在网络读上的生产者尽快
            # 收手，且不等待它结束（wait=False），否则消费侧会永久阻塞、流无法收敛。
            if stop_event is not None:
                stop_event.set()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            # 正常结束（已收到 sentinel）：生产者线程已自行退出，直接回收线程池。
            # 此处不置位 stop_event —— 上游据其判断"是否被用户中断"，
            # 误置会导致正常完成的回答不被写入多轮历史。
            executor.shutdown(wait=False, cancel_futures=True)

    def _do_stream_chat_tagged(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_event: Any | None = None,
    ) -> Iterator[tuple[str, str]]:
        """默认 tagged 实现：不区分推理，全部按正文产出。
        支持推理的提供商（如 OpenAI 兼容的 DeepSeek-R1/Qwen3）覆写此方法。"""
        import inspect

        if "stop_event" in inspect.signature(self._do_stream_chat).parameters:
            stream = self._do_stream_chat(messages, temperature, max_tokens, stop_event)
        else:
            stream = self._do_stream_chat(messages, temperature, max_tokens)
        for tok in stream:
            yield "content", tok
