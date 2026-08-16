"""OpenAI 兼容 API 客户端 — 通吃 DeepSeek / Qwen / OpenAI 等。

复用已有的 `openai` SDK（extras `api` 依赖组），
通过 `base_url` 区分不同服务商。
"""

from __future__ import annotations

from typing import Any, Iterator

from doc2mind.core.llm.base import LLMClient, LLMError

# OpenAI 兼容生态的 max_tokens 常见硬上限（sensenova 等网关实测 [1, 65536]）
_MAX_TOKENS_CEILING = 65536


def _sanitize_max_tokens(value: int | None) -> int | None:
    """max_tokens 超出兼容生态上限时返回 None（不传，由服务端取默认）。

    用户常把「上下文窗口」（如 256000）误当输出上限填进 llm_max_tokens，
    会被严格校验的网关 400 拒绝（field MaxTokens invalid）。不传该参数时
    服务端取模型默认上限，比硬 clamp 到某个常数对各服务商更通用。
    """
    if value is None:
        return None
    if value < 1:
        return 1
    if value > _MAX_TOKENS_CEILING:
        return None
    return value


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
        max_tokens: int = 2048,
        timeout: float = 120.0,
    ) -> None:
        self._model = model or "deepseek-chat"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._base_url = base_url

        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError(
                "OpenAI SDK 未安装。请在后端虚拟环境中运行：\n"
                "  pip install openai==2.38.0\n"
                "  或 pip install doc2mind[llm]"
            ) from e

        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "openai"

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
                max_tokens=_sanitize_max_tokens(
                    max_tokens if max_tokens is not None else self._max_tokens
                ),
            )
            if not resp.choices:
                raise LLMError("OpenAI API 返回空 choices")
            choice = resp.choices[0]
            content = choice.message.content
            if content is None:
                return ""
            return content.strip()
        except Exception as e:
            raise LLMError(f"OpenAI API 调用失败: {e}") from e

    def _do_stream_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=_sanitize_max_tokens(
                    max_tokens if max_tokens is not None else self._max_tokens
                ),
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            raise LLMError(f"OpenAI API 流式调用失败: {e}") from e
