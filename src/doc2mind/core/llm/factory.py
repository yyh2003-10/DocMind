"""LLM 客户端工厂 — 按配置创建对应实现。"""

from __future__ import annotations

from doc2mind.core.config import Settings
from doc2mind.core.llm.base import LLMClient
from doc2mind.core.llm.ollama_impl import OllamaClient
from doc2mind.core.llm.openai_impl import OpenAIClient


def get_llm_client(settings: Settings | None = None) -> LLMClient | None:
    """根据配置创建 LLM 客户端。

    Args:
        settings: 运行时配置，省略时用全局单例

    Returns:
        LLMClient 实例，provider 为 "none" 或未配置时返回 None
    """
    from doc2mind.core.config import get_settings

    s = settings or get_settings()
    provider = s.llm_provider or "none"

    if provider == "openai":
        if not s.llm_api_key:
            return None
        return OpenAIClient(
            api_key=s.llm_api_key,
            base_url=s.llm_base_url or None,
            model=s.llm_model or "deepseek-chat",
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
        )

    if provider == "ollama":
        return OllamaClient(
            model=s.llm_model or "llama3.2",
            host=s.llm_base_url or None,
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
        )

    return None
