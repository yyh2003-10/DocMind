"""Backend-neutral contracts for knowledge storage."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import Event
from typing import Any, Protocol, runtime_checkable


class StoreError(Exception):
    """A storage operation failed, independent of the storage backend."""


@dataclass(frozen=True)
class StoreCapabilities:
    """Backend features exposed without leaking implementation details."""

    backend: str
    vector_search: bool
    lexical_search: bool
    sparse_search: bool


@dataclass(frozen=True)
class StoreHealth:
    """Result of a read-only storage liveness check."""

    backend: str
    healthy: bool
    detail: str = ""


@runtime_checkable
class KnowledgeStore(Protocol):
    """The storage seam used by retrieval and document workflows.

    Collection filters accept one collection, several collections, or ``None``
    (no collection restriction). Search methods return ranked chunk IDs and
    backend-native scores; ``get_chunks`` resolves those IDs to stored chunks.
    """

    @property
    def sparse_available(self) -> bool: ...

    @property
    def dimension(self) -> int: ...

    @property
    def embedding_dim(self) -> int: ...

    @property
    def capabilities(self) -> StoreCapabilities: ...

    def health_check(self) -> StoreHealth: ...

    def open(self) -> None: ...

    def close(self) -> None: ...

    def vector_search(
        self,
        query_vec: Any,
        top_k: int = 10,
        collection: str | Sequence[str] | None = None,
    ) -> list[tuple[int, float]]: ...

    def bm25_search(
        self,
        query: str,
        top_k: int = 10,
        collection: str | Sequence[str] | None = None,
    ) -> list[tuple[int, float]]: ...

    def sparse_search(
        self,
        query: str,
        top_k: int = 10,
        collection: str | Sequence[str] | None = None,
    ) -> list[tuple[int, float]]: ...

    def get_chunks(self, chunk_ids: Sequence[int]) -> list[Any]: ...

    def upsert_document(self, doc: Any) -> None: ...

    def insert_chunks(
        self,
        document_id: str,
        collection: str,
        source: str,
        fmt: str,
        chunks: Sequence[Any],
        embeddings: Sequence[Any],
    ) -> int: ...

    def delete_document(self, document_id: str) -> int: ...

    def list_documents(
        self,
        collection: str | None = None,
        limit: int = 100,
        offset: int = 0,
        format: str | None = None,
        sort: str = "created_at_desc",
        q: str | None = None,
    ) -> list[Any]: ...

    # Ingestion and reindexing operations used by the application pipeline.
    def find_document_id_by_hash(self, file_hash: str, collection: str) -> str | None: ...

    def get_document_by_id(self, document_id: str) -> Any | None: ...

    def replace_document(
        self,
        doc: Any,
        chunks: Sequence[Any],
        embeddings: Sequence[Any],
    ) -> int: ...

    def list_chunks_by_document(self, document_id: str, limit: int = 100) -> list[Any]: ...

    def list_chunk_contents(
        self, collection: str | None = None
    ) -> list[tuple[int, str]]: ...

    def list_chunk_contexts(
        self, collection: str | None = None
    ) -> list[tuple[int, str, str | None]]: ...

    def update_embeddings(self, chunk_id_emb_pairs: Sequence[tuple[int, object]]) -> int: ...

    def rebuild_chunk_embeddings(
        self, chunk_id_emb_pairs: Sequence[tuple[int, object]], new_dim: int
    ) -> int: ...

    def purge_orphan_chunks(self) -> int: ...


@runtime_checkable
class IndexManager(Protocol):
    """Coordinates re-embedding and index replacement through a store seam.

    Implementations must not report success until the active vector index is
    usable. ``progress_callback`` receives completed and total chunk counts.
    """

    def reindex(
        self,
        *,
        store: KnowledgeStore,
        collection: str | None = None,
        model: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]: ...
