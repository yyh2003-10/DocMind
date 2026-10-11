"""LLM 客户端工厂 — 按配置创建对应实现。"""

from __future__ import annotations

from urllib.parse import urlparse

from doc2mind.core.config import Settings
from doc2mind.core.llm.base import LLMClient, LLMError

# 支持的 provider 标识（"none" = 未配置，不创建客户端）
SUPPORTED_PROVIDERS = ("none", "openai", "ollama", "anthropic", "gemini")


def is_local_base_url(base_url: str) -> bool:
    """本地/环回地址判定（免 key 兜底与 /v1 补全共用同一启发式口径）。"""
    u = base_url.lower()
    return (
        "localhost" in u
        or "127.0.0.1" in u
        or "[::1]" in u
        or ":1234" in u  # LM Studio 默认端口
        or ":8000" in u  # vLLM 默认端口
        or ":11434" in u  # Ollama OpenAI 兼容端点
    )


def normalize_openai_base_url(base_url: str | None) -> str | None:
    """OpenAI 兼容 base_url 归一化。

    本地运行时（LM Studio/Ollama）按根地址存储（如 http://127.0.0.1:1234，
    与其服务界面展示一致），这里对无路径的本地地址自动补 /v1 再交给 SDK；
    已带 /v1 或自定义路径（反向代理）的地址原样保留，云端地址不动。
    """
    if not base_url:
        return None
    url = base_url.strip().rstrip("/")
    if not url:
        return None
    if is_local_base_url(url) and not urlparse(url).path:
        return url + "/v1"
    return url


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
    timeout = s.llm_timeout if s.llm_timeout and s.llm_timeout > 0 else 180.0

    if provider == "none":
        return None

    if provider not in SUPPORTED_PROVIDERS:
        raise LLMError(
            f"不支持的 llm_provider: {provider!r}，可选值: {'/'.join(SUPPORTED_PROVIDERS)}"
        )

    if provider == "openai":
        api_key = s.llm_api_key
        base_url = normalize_openai_base_url(s.llm_base_url)
        if not api_key:
            if base_url and is_local_base_url(base_url):
                api_key = "lm-studio"  # LM Studio 等本地服务不校验 key，占位即可
            else:
                raise LLMError(
                    "llm_provider=openai 但 llm_api_key 未配置：远程 OpenAI 兼容服务"
                    f"（当前地址 {base_url or '未配置'}）必须填写 API Key；"
                    "若使用 LM Studio/Ollama 等本地服务，请检查服务地址是否为本机且服务已启动"
                )
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
