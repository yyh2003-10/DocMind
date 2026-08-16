"""OpenAI 兼容 API 客户端 — 通吃 DeepSeek / Qwen / OpenAI 等。

复用已有的 `openai` SDK（extras `api` 依赖组），
通过 `base_url` 区分不同服务商。
"""

from __future__ import annotations

from typing import Any, Iterator

from doc2mind.core.llm.base import LLMClient, LLMError, sanitize_max_tokens

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

    @staticmethod
    def _wrap_api_error(e: Exception, action: str) -> LLMError:
        """把 openai SDK 异常转成带原因分类的 LLMError（对齐 Anthropic/Gemini）。

        此前统一压成一句"OpenAI API 调用失败: ..."，用户无法区分
        401（key 无效）/ 404（模型或地址错）/ 429（限流）/ 网络不通。
        """
        status = getattr(e, "status_code", None)
        if status in (401, 403):
            hint = "API Key 无效或无权限"
        elif status == 404:
            hint = "模型名或 API 地址不存在（自定义 base_url 需含 /v1）"
        elif status == 429:
            hint = "请求过于频繁或额度不足"
        elif status is not None and 500 <= status < 600:
            hint = "服务端错误，请稍后重试"
        else:
            name = type(e).__name__
            if "Timeout" in name:
                hint = "请求超时，请检查网络或增加超时时间"
            elif "Connection" in name or "Connect" in name:
                hint = "无法连接 API 服务，请检查网络或 base_url"
            else:
                return LLMError(f"OpenAI API {action}失败: {e}")
        return LLMError(f"OpenAI API {action}失败（{hint}）: {e}")

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
            if not resp.choices:
                raise LLMError("OpenAI API 返回空 choices")
            choice = resp.choices[0]
            content = choice.message.content
            if content is None:
                return ""
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
        except LLMError:
            raise
        except Exception as e:
            raise self._wrap_api_error(e, "流式调用") from e
