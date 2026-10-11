"""Storage backend selection at application composition roots."""

from __future__ import annotations

from pathlib import Path

from doc2mind.core.store.contracts import KnowledgeStore, StoreError
from doc2mind.core.store.postgres_store import PostgresKnowledgeStore
from doc2mind.core.store.sqlite_vec import SqliteKnowledgeStore


def create_knowledge_store(
    settings: object,
    embedding_dim: int,
    *,
    db_path: Path | None = None,
) -> KnowledgeStore:
    """Construct the configured backend; never silently substitute a backend."""
    backend = str(getattr(settings, "storage_backend", "sqlite")).strip().lower()
    if backend == "postgres":
        profile = str(getattr(settings, "deployment_profile", "standard")).strip().lower()
        if profile not in {"standard", "enterprise"}:
            raise StoreError(
                f"deployment profile {profile!r} is incompatible with storage_backend='postgres'"
            )
        dsn = str(getattr(settings, "postgres_dsn", "")).strip()
        if not dsn:
            raise StoreError("PostgreSQL backend requires postgres_dsn or DOC2MIND_POSTGRES_DSN")
        return PostgresKnowledgeStore(
            dsn,
            embedding_dim,
            embedding_model=str(getattr(settings, "embed_model", "")),
            tenant_id=str(getattr(settings, "postgres_tenant_id", "default")),
            index_generation=int(getattr(settings, "postgres_index_generation", 1)),
        )
    if backend != "sqlite":
        raise StoreError(f"存储后端 {backend!r} 尚未实现；当前仅支持 storage_backend='sqlite'")
    profile = str(getattr(settings, "deployment_profile", "lite")).strip().lower()
    if profile != "lite":
        raise StoreError(f"部署档位 {profile!r} 尚未实现；当前仅支持 deployment_profile='lite'")

    path = db_path or settings.db_path
    return SqliteKnowledgeStore(
        path,
        embedding_dim,
        bm25_jieba_enabled=bool(getattr(settings, "bm25_jieba_enabled", False)),
        vector_quantize=str(getattr(settings, "vector_quantize", "none")),
        sparse_retrieval_enabled=bool(getattr(settings, "sparse_retrieval_enabled", False)),
    )
