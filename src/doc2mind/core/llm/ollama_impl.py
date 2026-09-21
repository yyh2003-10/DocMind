"""Ollama 本地客户端 — 通过 httpx 调用 Ollama REST API。

默认连接 http://localhost:11434，可通过环境变量 OLLAMA_HOST 自定义。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from typing import Any

from doc2mind.core.llm.base import (
    LLMClient,
    LLMError,
    is_transient_network_error,
    merge_stream_retry_text,
    sanitize_max_tokens,
)

logger = logging.getLogger(__name__)


def _options(temperature: float, max_tokens: int | None) -> dict:
    """构造 Ollama options：num_predict 超上限时不传（Ollama 取模型默认）。"""
    opts: dict = {"temperature": temperature}
    mt = sanitize_max_tokens(max_tokens)
    if mt is not None:
        opts["num_predict"] = mt
    return opts


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
        max_tokens: int = 8192,
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

    def list_models(self, timeout: float | None = None) -> list[str]:
        """GET /api/tags 列出本地已安装的模型（前端下拉选择用）。"""
        import httpx

        try:
            resp = httpx.get(
                f"{self._host}/api/tags",
                timeout=timeout if timeout and timeout > 0 else 10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            return names
        except httpx.HTTPStatusError as e:
            raise LLMError(
                f"Ollama 列出模型失败 (HTTP {e.response.status_code}): {e.response.text}"
            ) from e
        except httpx.RequestError as e:
            raise LLMError(
                f"无法连接 Ollama 服务 ({self._host})，请确认 Ollama 已启动: {e}"
            ) from e
        except Exception as e:
            raise LLMError(f"Ollama 列出模型失败: {e}") from e

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
            "options": _options(
                temperature if temperature is not None else self._temperature,
                max_tokens if max_tokens is not None else self._max_tokens,
            ),
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
            done_reason = data.get("done_reason")
            self._last_truncated = done_reason == "length"
            if done_reason == "length":
                logger.info(
                    "输出因达到 token 上限被截断（done_reason=length, model=%s）",
                    self._model,
                )
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

    def _is_thinking_model(self) -> bool:
        """按模型注册表判断是否为推理模型（决定是否请求 Ollama 输出思考链）。"""
        from doc2mind.core.llm.model_registry import get_model_spec

        return get_model_spec(self._model, self.provider).is_reasoning_model

    def _do_stream_chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_event: Any | None = None,
    ) -> Iterator[str]:
        """兼容入口：只吐正文 token（旧调用方/测试用）。"""
        for _kind, text in self._do_stream_chat_tagged(messages, temperature, max_tokens, stop_event):
            if text:
                yield text

    def _do_stream_chat_tagged(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop_event: Any | None = None,
    ) -> Iterator[tuple[str, str]]:
        """流式产出 (kind, text) 帧：思考链走 thinking 帧，正文走 content。
        带瞬时断连自动重试与平滑降级保护。"""
        import random
        import time

        import httpx

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

                payload = {
                    "model": self._model,
                    "messages": retry_messages,
                    "options": _options(
                        temperature if temperature is not None else self._temperature,
                        max_tokens if max_tokens is not None else self._max_tokens,
                    ),
                    "stream": True,
                }
                if self._is_thinking_model():
                    payload["think"] = True

                with httpx.Client(timeout=self._timeout) as client, client.stream(
                    "POST", f"{self._host}/api/chat", json=payload
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if stop_event is not None and stop_event.is_set():
                            return
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            msg = data.get("message", {})
                            thinking = msg.get("thinking")
                            if thinking:
                                yield ("thinking", thinking)
                            content = msg.get("content", "")
                            if content:
                                if retry_mode:
                                    content = merge_stream_retry_text("".join(emitted_parts), content)
                                    retry_mode = False
                                if content:
                                    emitted_parts.append(content)
                                    yield ("content", content)
                            if data.get("done", False):
                                self._last_truncated = data.get("done_reason") == "length"
                                if self._last_truncated:
                                    logger.info(
                                        "流式输出因达到 token 上限被截断（done_reason=length, model=%s）",
                                        self._model,
                                    )
                                break
                        except json.JSONDecodeError:
                            continue
                return
            except LLMError:
                raise
            except httpx.HTTPStatusError as e:
                raise LLMError(
                    f"Ollama API 流式返回错误 (HTTP {e.response.status_code}): {e.response.text}"
                ) from e
            except Exception as e:
                last_exc = e
                total_len = len("".join(emitted_parts))
                if attempt < max_attempts - 1 and is_transient_network_error(e):
                    time.sleep(backoff * (2**attempt) + random.uniform(0, 0.5))
                    retry_mode = True
                    continue
                if total_len >= 10 and is_transient_network_error(e):
                    logger.warning(
                        "Ollama 流式传输中途连接断开/抖动，已安全保留已生成的 %d 字符完整内容: %s",
                        total_len,
                        e,
                    )
                    return
                if isinstance(e, httpx.RequestError):
                    raise LLMError(
                        f"无法连接 Ollama 服务 ({self._host})，请确认 Ollama 已启动: {e}"
                    ) from e
                raise LLMError(f"Ollama 流式调用失败: {e}") from e

        if last_exc:
            raise LLMError(f"Ollama 流式调用失败: {last_exc}") from last_exc
