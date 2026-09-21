"""OpenAI 兼容 API 客户端 — 通吃 DeepSeek / Qwen / OpenAI 等。

复用已有的 `openai` SDK（extras `api` 依赖组），
通过 `base_url` 区分不同服务商。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from doc2mind.core.llm.base import (
    LLMClient,
    LLMError,
    is_provider_overloaded_error,
    is_transient_network_error,
    iter_exception_chain,
    merge_stream_retry_text,
    sanitize_max_tokens,
)

logger = logging.getLogger(__name__)

# 向后兼容别名（历史测试/调用方引用）
_sanitize_max_tokens = sanitize_max_tokens


class OpenAIClient(LLMClient):
    """OpenAI 兼容 API 客户端。

    Args:
        api_key: API 密钥
        base_url: API 端点（如 https://api.deepseek.com/v1）
        model: 模型名（如 deepseek-chat、gpt-4o-mini）
        temperature: 默认温度
        max_tokens: 默认最大 token 数
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = "deepseek-chat",
        temperature: float = 0.7,
        max_tokens: int = 8192,
        timeout: float = 120.0,
    ) -> None:
        self._model = model or "deepseek-chat"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._base_url = base_url

        try:
            import httpx
            from openai import OpenAI
        except ImportError as e:
            raise ImportError(
                "OpenAI SDK 未安装。请在后端虚拟环境中运行：\n"
                "  pip install openai==2.38.0\n"
                "  或 pip install doc2mind[llm]"
            ) from e

        # 显式配置健壮的 httpx.Client（禁用 http2 避免代理 RST 流，放宽 read 超时与 keepalive）
        http_client = httpx.Client(
            timeout=httpx.Timeout(timeout=timeout, connect=20.0, read=timeout, write=30.0),
            http2=False,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0),
        )
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "http_client": http_client,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client_kwargs = kwargs
        self._raw_api_key = api_key
        self._raw_base_url = base_url
        self._raw_timeout = timeout
        self._client = OpenAI(**kwargs)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "openai"

    @staticmethod
    def _wrap_api_error(e: Exception, action: str) -> LLMError:
        """把 openai SDK 异常转成带原因分类的 LLMError（对齐 Anthropic/Gemini）。

        此前统一压成一句"OpenAI API 调用失败: ..."，用户无法区分
        401（key 无效）/ 404（模型或地址错）/ 429（限流）/ 网络不通。
        """
        chain = iter_exception_chain(e)
        status = None
        for item in chain:
            s = getattr(item, "status_code", None)
            if s is not None:
                status = s
                break

        if status in (401, 403):
            hint = "API Key 无效或无权限"
        elif status == 404:
            if "Not found for account" in str(e):
                # NVIDIA NIM 下架模型的表现：/models 目录仍列出该模型，调用即 404
                # （Function '<id>': Not found for account '<acct>'）
                hint = "模型已下架或当前账号无权调用（模型列表可能仍显示），请更换模型"
            else:
                hint = "模型名或 API 地址不存在（自定义 base_url 需含 /v1）"
        elif status == 429:
            hint = "请求过于频繁或额度不足"
        elif status is not None and 500 <= status < 600:
            hint = "服务端错误，请稍后重试"
        elif is_provider_overloaded_error(e):
            hint = "上游服务临时过载，请稍后重试"
        elif is_transient_network_error(e):
            hint = "网络连接不稳定或代理超时，请检查网络或稍后重试"
        else:
            name = type(e).__name__
            if "Timeout" in name:
                hint = "请求超时，请检查网络或增加超时时间"
            elif "Connection" in name or "Connect" in name:
                hint = "无法连接 API 服务，请检查网络或 base_url"
            else:
                return LLMError(f"OpenAI API {action}失败: {e}")
        return LLMError(f"OpenAI API {action}失败（{hint}）: {e}")

    def list_models(self, timeout: float | None = None) -> list[str]:
        """GET /models 列出可用模型（DeepSeek / Qwen / OpenAI 等 OpenAI 兼容服务通用）。

        SDK 的 models.list 返回分页游标迭代器；此处取当前页（通常已含全部
        常用模型，下拉场景足够）。部分兼容服务未实现该接口（404），调用方
        应提示用户手动输入模型名。
        """
        try:
            resp = self._client.with_options(
                timeout=timeout if timeout and timeout > 0 else 10.0
            ).models.list()
            return sorted(m.id for m in resp.data if getattr(m, "id", None))
        except LLMError:
            raise
        except Exception as e:
            raise self._wrap_api_error(e, "列出模型") from e

    def _do_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=sanitize_max_tokens(
                    max_tokens if max_tokens is not None else self._max_tokens
                ),
            )
            choice = resp.choices[0]
            finish = getattr(choice, "finish_reason", None)
            self._last_truncated = finish == "length"
            if finish == "length":
                logger.info(
                    "输出因达到 token 上限被截断（finish_reason=length, model=%s）",
                    self._model,
                )
            msg = choice.message
            content = getattr(msg, "content", None) or ""
            return content.strip()
        except LLMError:
            raise
        except Exception as e:
            raise self._wrap_api_error(e, "调用") from e

    def _do_stream_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_event: Any | None = None,
    ) -> Iterator[str]:
        """兼容入口：只吐正文 token（旧调用方/测试用）。"""
        for _kind, text in self._stream_completions_tagged(messages, temperature, max_tokens, stop_event):
            if text:
                yield text

    def _do_stream_chat_tagged(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_event: Any | None = None,
    ) -> Iterator[tuple[str, str]]:
        """流式产出 (kind, text) 帧：推理链 thinking + 正文 content。

        OpenAI 兼容的推理模型（DeepSeek-R1 / Qwen3 / Kimi 等）在流式 delta 中
        提供 `reasoning_content`（推理链）与 `content`（正文）；普通模型只有
        content，此时全部按正文处理，行为与旧实现一致。
        """
        yield from self._stream_completions_tagged(messages, temperature, max_tokens, stop_event)

    def _stream_completions_tagged(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_event: Any | None = None,
    ) -> Iterator[tuple[str, str]]:
        max_attempts = 3
        backoff = 1.0
        last_exc: Exception | None = None
        emitted_parts: list[str] = []
        retry_mode = False

        calc_max_tokens = _sanitize_max_tokens(
            max_tokens if max_tokens is not None else self._max_tokens
        )
        if calc_max_tokens is None or calc_max_tokens < 4096:
            calc_max_tokens = 8192

        for attempt in range(max_attempts):
            try:
                retry_messages = list(messages)
                if emitted_parts:
                    # 断点续传：将已输出正文作为 assistant 上下文，无缝接着上一句话续写
                    retry_messages.append({"role": "assistant", "content": "".join(emitted_parts)})
                    retry_messages.append({
                        "role": "user",
                        "content": "请从上述已生成的末尾直接无缝继续写，不要重复已生成内容，直接输出后续正文：",
                    })

                stream = self._client.chat.completions.create(
                    model=self._model,
                    messages=retry_messages,
                    temperature=temperature if temperature is not None else self._temperature,
                    max_tokens=calc_max_tokens,
                    stream=True,
                )
                last_finish_reason: str | None = None
                for chunk in stream:
                    if stop_event is not None and stop_event.is_set():
                        try:
                            stream.close()
                        except Exception:  # noqa: BLE001
                            pass
                        return
                    choices = getattr(chunk, "choices", None)
                    if not choices:
                        continue
                    choice = choices[0]
                    finish = getattr(choice, "finish_reason", None)
                    if finish:
                        last_finish_reason = finish
                    delta = getattr(choice, "delta", None)
                    if not delta:
                        continue
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield "thinking", reasoning
                    content = getattr(delta, "content", None)
                    if content:
                        if retry_mode:
                            content = merge_stream_retry_text("".join(emitted_parts), content)
                            retry_mode = False
                        if content:
                            emitted_parts.append(content)
                            yield "content", content

                # 若因达到单次 token 上限 (length) 截断，自动无缝续写补全
                if last_finish_reason == "length" and attempt < max_attempts - 1:
                    logger.info("输出达到单次 token 上限(length)，自动发起下一轮无缝续写补全...")
                    continue

                # 续写耗尽仍 length（或正常 finish=stop）→ 上报截断标记供 T6.1 消费
                self._last_truncated = last_finish_reason == "length"
                if self._last_truncated:
                    logger.info(
                        "续写达上限仍被截断（finish_reason=length, model=%s），上报截断标记",
                        self._model,
                    )
                return
            except LLMError:
                raise
            except Exception as e:
                last_exc = e
                total_emitted_len = len("".join(emitted_parts))

                # 若因瞬时网络抖动中断，且还有重试配额，先尝试重建连接并续写后半段
                if attempt < max_attempts - 1 and self._is_transient_stream_error(e):
                    import random
                    import time

                    import httpx
                    from openai import OpenAI

                    try:
                        self._client.close()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        http_client = httpx.Client(
                            timeout=httpx.Timeout(timeout=self._raw_timeout, connect=20.0, read=self._raw_timeout, write=30.0),
                            http2=False,
                            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0),
                        )
                        kwargs = dict(self._client_kwargs)
                        kwargs["http_client"] = http_client
                        self._client = OpenAI(**kwargs)
                    except Exception:  # noqa: BLE001
                        pass

                    time.sleep(backoff * (2**attempt) + random.uniform(0, 0.5))
                    retry_mode = True
                    continue

                # 若重试耗尽但已产出足量正文（>= 10 字符），安全平滑收尾，确保已生成内容完整呈现
                if total_emitted_len >= 10 and self._is_transient_stream_error(e):
                    logger.warning(
                        "流式传输中途连接由对端关闭/抖动中断，已安全保留已生成的 %d 字符完整内容: %s",
                        total_emitted_len,
                        e,
                    )
                    return

                raise self._wrap_api_error(e, "流式调用") from e
        # 不可达（循环内必然 raise 或 return），仅为类型检查兜底
        raise self._wrap_api_error(last_exc, "流式调用")

    @staticmethod
    def _is_transient_stream_error(e: Exception) -> bool:
        """判断是否为可重试的瞬时错误（网络抖动 / 上游过载）。"""
        return is_transient_network_error(e) or is_provider_overloaded_error(e)
