"""Anthropic 官方 API 客户端 — 通过 httpx 调用 /v1/messages 接口。

不依赖 anthropic SDK（httpx 已是核心依赖），Claude 系列 API 与
OpenAI 格式不兼容，需要单独实现消息转换与 SSE 解析。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx

from doc2mind.core.llm.base import (
    LLMClient,
    LLMError,
    is_transient_network_error,
    merge_stream_retry_text,
    sanitize_max_tokens,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_BASE_URL = "https://api.anthropic.com"
# Anthropic 的 max_tokens 是必填字段（无服务端默认）；用户误填超大值时
# sanitize 返回 None，此处退回所有 Claude 模型都接受的安全上限。
_FALLBACK_MAX_TOKENS = 8192


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """把 OpenAI 格式消息拆成 (system 文本, 其余消息)。

    Anthropic 的 system 提示是顶层字段而非消息列表中的一条。
    """
    system_parts: list[str] = []
    rest: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                system_parts.append(content.strip())
        else:
            rest.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    return "\n\n".join(system_parts), rest


class AnthropicClient(LLMClient):
    """Anthropic Claude 客户端。

    Args:
        api_key: Anthropic API Key（sk-ant- 开头）
        base_url: API 地址，默认 https://api.anthropic.com
        model: 模型名（如 claude-sonnet-4-5、claude-3-5-haiku-latest）
        temperature: 默认温度
        max_tokens: 默认最大 token 数（Anthropic 必填，无服务端默认）
        timeout: HTTP 请求超时秒数
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = "claude-sonnet-4-5",
        temperature: float = 0.7,
        max_tokens: int = 8192,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._model = model or "claude-sonnet-4-5"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "anthropic"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _enable_thinking(self) -> bool:
        """是否启用 extended thinking：注册表标记的推理模型（如 claude-3-7-sonnet）
        或名字显式含 thinking 的变体。其余 Claude 型号不带 thinking 参数，
        保持与不支持该特性的网关/代理兼容。"""
        from doc2mind.core.llm.model_registry import get_model_spec

        return (
            get_model_spec(self._model, self.provider).is_reasoning_model
            or "thinking" in self._model.lower()
        )

    def _payload(
        self,
        messages: list[dict],
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict:
        system, rest = _split_system(messages)
        mt = sanitize_max_tokens(
            max_tokens if max_tokens is not None else self._max_tokens
        )
        payload: dict = {
            "model": self._model,
            "max_tokens": mt if mt is not None else _FALLBACK_MAX_TOKENS,
            "temperature": temperature if temperature is not None else self._temperature,
            "messages": rest,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        if self._enable_thinking():
            total = payload["max_tokens"]
            # API 约束：1024 <= budget_tokens < max_tokens；不满足时宁可不启用
            if total > 1024:
                payload["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": min(max(1024, total // 2), total - 1),
                }
                # extended thinking 要求 temperature=1；省略即服务端默认值
                payload.pop("temperature", None)
        return payload

    def list_models(self, timeout: float | None = None) -> list[str]:
        """GET /v1/models 列出可用 Claude 模型（取首页，下拉场景足够）。"""
        import httpx

        try:
            resp = httpx.get(
                f"{self._base_url}/v1/models",
                headers=self._headers(),
                timeout=timeout if timeout and timeout > 0 else 10.0,
            )
            self._raise_for_status(resp)
            data = resp.json()
            return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
        except LLMError:
            raise
        except httpx.RequestError as e:
            raise LLMError(
                f"无法连接 Anthropic API ({self._base_url})，请检查网络或 API 地址: {e}"
            ) from e
        except Exception as e:
            raise LLMError(f"Anthropic 列出模型失败: {e}") from e

    def _do_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        import httpx

        url = f"{self._base_url}/v1/messages"
        try:
            resp = httpx.post(
                url,
                json=self._payload(messages, temperature, max_tokens, stream=False),
                headers=self._headers(),
                timeout=self._timeout,
            )
            self._raise_for_status(resp)
            data = resp.json()
            texts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
            return "".join(texts).strip()
        except LLMError:
            raise
        except httpx.RequestError as e:
            raise LLMError(
                f"无法连接 Anthropic API ({self._base_url})，请检查网络或 API 地址: {e}"
            ) from e
        except Exception as e:
            raise LLMError(f"Anthropic API 调用失败: {e}") from e

    def _do_stream_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_event: Any | None = None,
    ) -> Iterator[str]:
        """兼容入口：只吐正文 token（旧调用方/测试用）。"""
        for kind, text in self._do_stream_chat_tagged(messages, temperature, max_tokens, stop_event):
            if kind == "content" and text:
                yield text

    def _do_stream_chat_tagged(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_event: Any | None = None,
    ) -> Iterator[tuple[str, str]]:
        """流式产出 (kind, text) 帧：thinking_delta（extended thinking 思考链）
        走 thinking 帧，text_delta 正文走 content。未启用 thinking 的请求
        只会有 text_delta，行为与旧实现一致。带网络抖动自动重试与平滑收尾保护。"""
        import random
        import time

        url = f"{self._base_url}/v1/messages"
        max_attempts = 3
        backoff = 1.0
        emitted_parts: list[str] = []
        last_exc: Exception | None = None
        retry_mode = False

        for attempt in range(max_attempts):
            try:
                retry_messages = list(messages)
                if emitted_parts:
                    # 断点续传：将已生成正文加入上下文无缝续写
                    retry_messages.append({"role": "assistant", "content": "".join(emitted_parts)})
                    retry_messages.append({
                        "role": "user",
                        "content": "请从上述已生成的末尾直接无缝继续写，不要重复已生成内容，直接输出后续正文：",
                    })

                with httpx.Client(timeout=self._timeout) as client, client.stream(
                    "POST",
                    url,
                    json=self._payload(retry_messages, temperature, max_tokens, stream=True),
                    headers=self._headers(),
                ) as response:
                    self._raise_for_status(response)
                    for line in response.iter_lines():
                        if stop_event is not None and stop_event.is_set():
                            return
                        if not line.startswith("data:"):
                            continue
                        raw = line[len("data:"):].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "thinking_delta":
                                thought = delta.get("thinking", "")
                                if thought:
                                    yield ("thinking", thought)
                            else:
                                text = delta.get("text", "")
                                if text:
                                    if retry_mode:
                                        text = merge_stream_retry_text("".join(emitted_parts), text)
                                        retry_mode = False
                                    if text:
                                        emitted_parts.append(text)
                                        yield ("content", text)
                        elif event.get("type") == "error":
                            raise LLMError(f"Anthropic 流式返回错误: {event.get('error', {}).get('message', raw)}")
                return
            except LLMError:
                raise
            except Exception as e:
                last_exc = e
                total_len = len("".join(emitted_parts))
                if attempt < max_attempts - 1 and is_transient_network_error(e):
                    time.sleep(backoff * (2**attempt) + random.uniform(0, 0.5))
                    retry_mode = True
                    continue
                if total_len >= 10 and is_transient_network_error(e):
                    logger.warning(
                        "Anthropic 流式传输中途连接断开/抖动，已安全保留已生成的 %d 字符完整内容: %s",
                        total_len,
                        e,
                    )
                    return
                if isinstance(e, httpx.RequestError):
                    raise LLMError(
                        f"无法连接 Anthropic API ({self._base_url})，请检查网络或 API 地址: {e}"
                    ) from e
                raise LLMError(f"Anthropic 流式调用失败: {e}") from e

        if last_exc:
            raise LLMError(f"Anthropic 流式调用失败: {last_exc}") from last_exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """把 HTTP 错误转成带原因的 LLMError（401 → key 无效等）。"""
        if resp.is_success:
            return
        status = resp.status_code
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:  # noqa: BLE001 — 响应体不是 JSON 时退回文本
            detail = resp.text[:200]
        if status in (401, 403):
            hint = "API Key 无效或无权限"
        elif status == 404:
            hint = "API 地址或模型名不存在"
        elif status == 429:
            hint = "请求过于频繁或额度不足"
        else:
            hint = "API 返回错误"
        raise LLMError(f"Anthropic API {hint} (HTTP {status}): {detail}")
