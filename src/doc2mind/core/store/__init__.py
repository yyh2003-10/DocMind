"""存储包 — 向量 + 元数据存储（sqlite-vec）。"""

from __future__ import annotations

from doc2mind.core.store.sqlite_vec import StoreError, StoreStats, VectorStore

__all__ = [
    "VectorStore",
    "StoreError",
    "StoreStats",
]
