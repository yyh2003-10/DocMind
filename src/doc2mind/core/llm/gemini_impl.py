"""Google Gemini 客户端 — 通过 httpx 调用 generateContent 接口。

不依赖 google-genai SDK（httpx 已是核心依赖），
API Key 走 x-goog-api-key 请求头（不进 URL，避免日志泄漏）。
"""

from __future__ import annotations

import json
from typing import Iterator

from doc2mind.core.llm.base import LLMClient, LLMError, sanitize_max_tokens

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

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
        max_tokens: int = 2048,
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
        payload: dict = {
            "contents": contents,
            "generationConfig": gen_config,
        }
        if system is not None:
            payload["systemInstruction"] = system
        return payload

    @staticmethod
    def _extract_text(data: dict) -> str:
        """从 generateContent 响应中提取回复文本。"""
        candidates = data.get("candidates") or []
        if not candidates:
            # 无 candidates 时可能是安全策略拦截，把 promptFeedback 带出来
            feedback = data.get("promptFeedback", {})
            block = feedback.get("blockReason")
            if block:
                raise LLMError(f"Gemini 拒绝了该请求（安全策略: {block}）")
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

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
    ) -> Iterator[str]:
        import httpx

        url = f"{self._base_url}/v1beta/models/{self._model}:streamGenerateContent?alt=sse"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                with client.stream(
                    "POST",
                    url,
                    json=self._payload(messages, temperature, max_tokens),
                    headers=self._headers(),
                ) as response:
                    self._raise_for_status(response)
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[len("data:"):].strip()
                        if not raw:
                            continue
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        text = self._extract_text(event)
                        if text:
                            yield text
        except LLMError:
            raise
        except httpx.RequestError as e:
            raise LLMError(
                f"无法连接 Gemini API ({self._base_url})，请检查网络或 API 地址: {e}"
            ) from e
        except Exception as e:
            raise LLMError(f"Gemini 流式调用失败: {e}") from e

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
