"""Google Gemini 客户端 — 通过 httpx 调用 generateContent 接口。

不依赖 google-genai SDK（httpx 已是核心依赖），
API Key 走 x-goog-api-key 请求头（不进 URL，避免日志泄漏）。
"""

from __future__ import annotations

import json
import logging
import re
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

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

# 支持 thinkingConfig.includeThoughts 的型号特征（Gemini 2.5 系列原生支持；
# 更早系列带上该参数会被 API 以 400 拒绝，必须门控）
_THOUGHT_CAPABLE_RE = re.compile(r"(gemini-2\.5|thinking)", re.I)

_ROLE_MAP = {"user": "user", "assistant": "model", "system": "user"}


def _to_contents(messages: list[dict]) -> tuple[list[dict] | None, list[dict]]:
    """把 OpenAI 格式消息转成 Gemini 格式。

    Returns:
        (systemInstruction, contents)；system 消息抽成顶层 systemInstruction
    """
    system_parts: list[str] = []
    contents: list[dict] = []
    for m in messages:
        content = m.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        role = m.get("role", "user")
        if role == "system":
            system_parts.append(content.strip())
        else:
            contents.append({"role": _ROLE_MAP.get(role, "user"), "parts": [{"text": content}]})
    system = None
    if system_parts:
        system = {"parts": [{"text": "\n\n".join(system_parts)}]}
    return system, contents


class GeminiClient(LLMClient):
    """Google Gemini 客户端。

    Args:
        api_key: Google AI Studio API Key
        base_url: API 地址，默认 https://generativelanguage.googleapis.com
        model: 模型名（如 gemini-2.5-flash、gemini-2.5-pro）
        temperature: 默认温度
        max_tokens: 默认最大输出 token 数
        timeout: HTTP 请求超时秒数
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = "gemini-2.5-flash",
        temperature: float = 0.7,
        max_tokens: int = 8192,
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._model = model or "gemini-2.5-flash"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "gemini"

    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }

    def _payload(
        self,
        messages: list[dict],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict:
        system, contents = _to_contents(messages)
        gen_config: dict = {
            "temperature": temperature if temperature is not None else self._temperature,
        }
        # 超上限（用户误填上下文窗口大小）时不传，由 Gemini 取模型默认上限
        mt = sanitize_max_tokens(
            max_tokens if max_tokens is not None else self._max_tokens
        )
        if mt is not None:
            gen_config["maxOutputTokens"] = mt
        if self._include_thoughts():
            gen_config["thinkingConfig"] = {"includeThoughts": True}
        payload: dict = {
            "contents": contents,
            "generationConfig": gen_config,
        }
        if system is not None:
            payload["systemInstruction"] = system
        return payload

    def _include_thoughts(self) -> bool:
        """是否请求思考摘要：Gemini 2.5 系列 / 注册表推理模型 / 名字含 thinking。
        其余型号带 includeThoughts 会被 API 拒绝，必须门控。"""
        from doc2mind.core.llm.model_registry import get_model_spec

        return bool(
            _THOUGHT_CAPABLE_RE.search(self._model)
            or get_model_spec(self._model, self.provider).is_reasoning_model
        )

    @staticmethod
    def _extract_parts(data: dict) -> list[tuple[str, str]]:
        """提取 (kind, text) 列表：thought part → thinking，其余 → content。

        无 candidates 且被安全策略拦截时抛 LLMError（与旧实现一致）。"""
        candidates = data.get("candidates") or []
        if not candidates:
            # 无 candidates 时可能是安全策略拦截，把 promptFeedback 带出来
            feedback = data.get("promptFeedback", {})
            block = feedback.get("blockReason")
            if block:
                raise LLMError(f"Gemini 拒绝了该请求（安全策略: {block}）")
            return []
        parts = candidates[0].get("content", {}).get("parts", [])
        result: list[tuple[str, str]] = []
        for p in parts:
            text = p.get("text", "")
            if not text:
                continue
            result.append(("thinking" if p.get("thought") else "content", text))
        return result

    @classmethod
    def _extract_text(cls, data: dict) -> str:
        """正文文本（排除 thought part；非流式路径用，回答不含思考链）。"""
        return "".join(t for kind, t in cls._extract_parts(data) if kind == "content")

    def list_models(self, timeout: float | None = None) -> list[str]:
        """GET /v1beta/models 列出支持 generateContent 的模型（去 models/ 前缀）。"""
        import httpx

        url = f"{self._base_url}/v1beta/models?pageSize=1000"
        try:
            resp = httpx.get(
                url,
                headers=self._headers(),
                timeout=timeout if timeout and timeout > 0 else 10.0,
            )
            self._raise_for_status(resp)
            names: list[str] = []
            for m in resp.json().get("models", []):
                if "generateContent" in (m.get("supportedGenerationMethods") or []):
                    name = m.get("name", "")
                    if name.startswith("models/"):
                        name = name[len("models/"):]
                    if name:
                        names.append(name)
            return sorted(names)
        except LLMError:
            raise
        except httpx.RequestError as e:
            raise LLMError(
                f"无法连接 Gemini API ({self._base_url})，请检查网络或 API 地址: {e}"
            ) from e
        except Exception as e:
            raise LLMError(f"Gemini 列出模型失败: {e}") from e

    def _do_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        import httpx

        url = f"{self._base_url}/v1beta/models/{self._model}:generateContent"
        try:
            resp = httpx.post(
                url,
                json=self._payload(messages, temperature, max_tokens),
                headers=self._headers(),
                timeout=self._timeout,
            )
            self._raise_for_status(resp)
            return self._extract_text(resp.json()).strip()
        except LLMError:
            raise
        except httpx.RequestError as e:
            raise LLMError(
                f"无法连接 Gemini API ({self._base_url})，请检查网络或 API 地址: {e}"
            ) from e
        except Exception as e:
            raise LLMError(f"Gemini API 调用失败: {e}") from e

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
        """流式产出 (kind, text) 帧：thought part 走 thinking 帧，正文走 content。
        未启用 includeThoughts 的请求不会有 thought part，行为与旧实现一致。带瞬时网络断连重试与平滑降级。"""
        import random
        import time

        url = f"{self._base_url}/v1beta/models/{self._model}:streamGenerateContent?alt=sse"
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
                    json=self._payload(retry_messages, temperature, max_tokens),
                    headers=self._headers(),
                ) as response:
                    self._raise_for_status(response)
                    for line in response.iter_lines():
                        if stop_event is not None and stop_event.is_set():
                            return
                        if not line.startswith("data:"):
                            continue
                        raw = line[len("data:"):].strip()
                        if not raw:
                            continue
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        for kind, text in self._extract_parts(event):
                            if kind == "content" and text:
                                if retry_mode:
                                    text = merge_stream_retry_text("".join(emitted_parts), text)
                                    retry_mode = False
                                if text:
                                    emitted_parts.append(text)
                            if text:
                                yield (kind, text)
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
                        "Gemini 流式传输中途连接断开/抖动，已安全保留已生成的 %d 字符完整内容: %s",
                        total_len,
                        e,
                    )
                    return
                if isinstance(e, httpx.RequestError):
                    raise LLMError(
                        f"无法连接 Gemini API ({self._base_url})，请检查网络或 API 地址: {e}"
                    ) from e
                raise LLMError(f"Gemini 流式调用失败: {e}") from e

        if last_exc:
            raise LLMError(f"Gemini 流式调用失败: {last_exc}") from last_exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """把 HTTP 错误转成带原因的 LLMError。"""
        if resp.is_success:
            return
        status = resp.status_code
        try:
            body = resp.json()
            detail = body.get("error", {}).get("message", resp.text[:200])
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
        raise LLMError(f"Gemini API {hint} (HTTP {status}): {detail}")
