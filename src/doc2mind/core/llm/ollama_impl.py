"""Ollama 本地客户端 — 通过 httpx 调用 Ollama REST API。

默认连接 http://localhost:11434，可通过环境变量 OLLAMA_HOST 自定义。
"""

from __future__ import annotations

import json
import os
from typing import Iterator

from doc2mind.core.llm.base import LLMClient, LLMError


class OllamaClient(LLMClient):
    """Ollama 本地客户端。

    Args:
        model: 模型名（如 llama3.2、qwen2.5、deepseek-r1）
        host: Ollama 服务地址，默认 http://localhost:11434
        temperature: 默认温度
        max_tokens: 默认最大 token 数
    """

    def __init__(
        self,
        model: str = "llama3.2",
        host: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: float = 120.0,
    ) -> None:
        self._model = model or "llama3.2"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "ollama"

    def _do_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        import httpx

        payload = {
            "model": self._model,
            "messages": messages,
            "options": {
                "temperature": temperature if temperature is not None else self._temperature,
                "num_predict": max_tokens if max_tokens is not None else self._max_tokens,
            },
            "stream": False,
        }
        try:
            resp = httpx.post(
                f"{self._host}/api/chat",
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            return content.strip()
        except httpx.HTTPStatusError as e:
            raise LLMError(
                f"Ollama API 返回错误 (HTTP {e.response.status_code}): {e.response.text}"
            ) from e
        except httpx.RequestError as e:
            raise LLMError(
                f"无法连接 Ollama 服务 ({self._host})，请确认 Ollama 已启动: {e}"
            ) from e
        except Exception as e:
            raise LLMError(f"Ollama 调用失败: {e}") from e

    def _do_stream_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        import httpx

        payload = {
            "model": self._model,
            "messages": messages,
            "options": {
                "temperature": temperature if temperature is not None else self._temperature,
                "num_predict": max_tokens if max_tokens is not None else self._max_tokens,
            },
            "stream": True,
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                with client.stream("POST", f"{self._host}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            content = data.get("message", {}).get("content", "")
                            if content:
                                yield content
                            if data.get("done", False):
                                break
                        except (json.JSONDecodeError, KeyError):
                            continue
        except httpx.HTTPStatusError as e:
            raise LLMError(
                f"Ollama API 流式返回错误 (HTTP {e.response.status_code}): {e.response.text}"
            ) from e
        except httpx.RequestError as e:
            raise LLMError(
                f"无法连接 Ollama 服务 ({self._host})，请确认 Ollama 已启动: {e}"
            ) from e
        except Exception as e:
            raise LLMError(f"Ollama 流式调用失败: {e}") from e
