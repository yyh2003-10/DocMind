"""存储包 — 向量 + 元数据存储（sqlite-vec）。"""

from __future__ import annotations

from doc2mind.core.job_store import InMemoryJobStore, JobStore
from doc2mind.core.store.contracts import (
    IndexManager,
    KnowledgeStore,
    StoreCapabilities,
    StoreError,
    StoreHealth,
)
from doc2mind.core.store.factory import create_knowledge_store
from doc2mind.core.store.postgres_store import PostgresKnowledgeStore
from doc2mind.core.store.sqlite_vec import (
    SqliteKnowledgeStore,
    StoreStats,
    VectorStore,
)

__all__ = [
    "KnowledgeStore",
    "IndexManager",
    "JobStore",
    "InMemoryJobStore",
    "StoreCapabilities",
    "StoreHealth",
    "create_knowledge_store",
    "SqliteKnowledgeStore",
    "PostgresKnowledgeStore",
    "VectorStore",
    "StoreError",
    "StoreStats",
]
