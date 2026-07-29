"""分块器包 — 把 `LoadedDocument.elements` 切成可嵌入的 `Chunk`。

三种分块策略组合使用：
    semantic  → 文档主体（标题边界 / 段落合并 / 滑窗）
    table     → 表格保护（整表一块，跨页合并）
    code      → 代码块按函数/类切分

入口：`chunk_document(doc, settings) -> list[Chunk]`
"""

from __future__ import annotations

from doc2mind.core.chunker.base import Chunk, Chunker, ChunkerError
from doc2mind.core.chunker.chunker_pipeline import chunk_document

__all__ = [
    "Chunk",
    "Chunker",
    "ChunkerError",
    "chunk_document",
]
