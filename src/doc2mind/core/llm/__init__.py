"""LLM 客户端模块 — 统一抽象 + OpenAI / Ollama 实现。

使用方式：
    from doc2mind.core.llm import get_llm_client, LLMClient, LLMError
    client = get_llm_client(settings)
    if client:
        reply = client.chat([{"role": "user", "content": "你好"}])
"""

from doc2mind.core.llm.base import LLMClient, LLMError, LLMTimeoutError
from doc2mind.core.llm.factory import get_llm_client

__all__ = ["LLMClient", "LLMError", "LLMTimeoutError", "get_llm_client"]
