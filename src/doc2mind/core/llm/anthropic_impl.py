"""Anthropic 官方 API 客户端 — 通过 httpx 调用 /v1/messages 接口。

不依赖 anthropic SDK（httpx 已是核心依赖），Claude 系列 API 与
OpenAI 格式不兼容，需要单独实现消息转换与 SSE 解析。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from doc2mind.core.llm.base import LLMClient, LLMError, sanitize_max_tokens

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
        max_tokens: int = 2048,
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
    ) -> Iterator[str]:
        import httpx

        url = f"{self._base_url}/v1/messages"
        try:
            with httpx.Client(timeout=self._timeout) as client, client.stream(
                    "POST",
                    url,
                    json=self._payload(messages, temperature, max_tokens, stream=True),
                    headers=self._headers(),
                ) as response:
                    self._raise_for_status(response)
                    for line in response.iter_lines():
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
                            text = event.get("delta", {}).get("text", "")
                            if text:
                                yield text
                        elif event.get("type") == "error":
                            raise LLMError(f"Anthropic 流式返回错误: {event.get('error', {}).get('message', raw)}")
        except LLMError:
            raise
        except httpx.RequestError as e:
            raise LLMError(
                f"无法连接 Anthropic API ({self._base_url})，请检查网络或 API 地址: {e}"
            ) from e
        except Exception as e:
            raise LLMError(f"Anthropic 流式调用失败: {e}") from e

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
