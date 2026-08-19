"""LLM 客户端工厂 — 按配置创建对应实现。"""

from __future__ import annotations

from doc2mind.core.config import Settings
from doc2mind.core.llm.base import LLMClient, LLMError

# 支持的 provider 标识（"none" = 未配置，不创建客户端）
SUPPORTED_PROVIDERS = ("none", "openai", "ollama", "anthropic", "gemini")


def get_llm_client(settings: Settings | None = None) -> LLMClient | None:
    """根据配置创建 LLM 客户端。

    Args:
        settings: 运行时配置，省略时用全局单例

    Returns:
        LLMClient 实例；provider 为 "none" 时返回 None

    Raises:
        LLMError: provider 不在 SUPPORTED_PROVIDERS 中（配置错误应尽早暴露，
            而不是静默返回 None 让对话时才报错）
    """
    from doc2mind.core.config import get_settings

    s = settings or get_settings()
    provider = s.llm_provider or "none"
    timeout = s.llm_timeout if s.llm_timeout and s.llm_timeout > 0 else 120.0

    if provider == "none":
        return None

    if provider not in SUPPORTED_PROVIDERS:
        raise LLMError(
            f"不支持的 llm_provider: {provider!r}，可选值: {'/'.join(SUPPORTED_PROVIDERS)}"
        )

    if provider == "openai":
        api_key = s.llm_api_key
        base_url = s.llm_base_url or None
        if not api_key:
            if base_url and ("localhost" in base_url or "127.0.0.1" in base_url or ":1234" in base_url or ":8000" in base_url):
                api_key = "lm-studio"
            else:
                raise LLMError("llm_provider=openai 但 llm_api_key 未配置")
        from doc2mind.core.llm.openai_impl import OpenAIClient

        return OpenAIClient(
            api_key=api_key,
            base_url=base_url,
            model=s.llm_model or "deepseek-chat",
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
            timeout=timeout,
        )

    if provider == "anthropic":
        if not s.llm_api_key:
            raise LLMError("llm_provider=anthropic 但 llm_api_key 未配置")
        from doc2mind.core.llm.anthropic_impl import AnthropicClient

        return AnthropicClient(
            api_key=s.llm_api_key,
            base_url=s.llm_base_url or None,
            model=s.llm_model or "claude-sonnet-4-5",
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
            timeout=timeout,
        )

    if provider == "gemini":
        if not s.llm_api_key:
            raise LLMError("llm_provider=gemini 但 llm_api_key 未配置")
        from doc2mind.core.llm.gemini_impl import GeminiClient

        return GeminiClient(
            api_key=s.llm_api_key,
            base_url=s.llm_base_url or None,
            model=s.llm_model or "gemini-2.5-flash",
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
            timeout=timeout,
        )

    if provider == "ollama":
        from doc2mind.core.llm.ollama_impl import OllamaClient

        return OllamaClient(
            model=s.llm_model or "llama3.2",
            host=s.llm_base_url or None,
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
            timeout=timeout,
        )

    return None
