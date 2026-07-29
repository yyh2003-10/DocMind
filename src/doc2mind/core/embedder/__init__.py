"""嵌入引擎包 — 把 `Chunk` 转成向量。

两种实现：
    fastembed_impl  → ONNX 本地嵌入（core，默认）
    api_impl        → OpenAI 兼容 API（extras `api`）

入口：`get_embedder(settings)` 按配置返回实例。
"""

from __future__ import annotations

from doc2mind.core.embedder.base import Embedder, EmbedderError
from doc2mind.core.embedder.factory import get_embedder

__all__ = [
    "Embedder",
    "EmbedderError",
    "get_embedder",
]
