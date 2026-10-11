"""Transactional PostgreSQL writer for complete document bundles.

This is a write-side building block, not a ``KnowledgeStore`` adapter. The
application owns connection and pool lifecycle; this module owns validation,
batch shaping, and the transaction that replaces one active source document.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any, Protocol

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.store.contracts import StoreError


class DocumentRecord(Protocol):
    """Backend-neutral document fields required by the writer."""

    id: str
    source: str
    collection: str
    format: str
    file_hash: str
    size_bytes: int
    page_count: int | None
    title: str | None
    tags: list[str] | None
    summary: str | None
    enriched_at: str | None
    created_at: str
    updated_at: str


_CHUNK_COLUMNS = (
    "document_id, document_version, collection, tenant_id, source, format, "
    "content, tokens, chunk_index, doc_type, page, sheet, slide, heading, "
    "language, extra"
)
_CHUNK_ROW_WIDTH = 16
_KNOWN_CHUNK_METADATA = {
    "type",
    "page",
    "sheet",
    "slide",
    "heading",
    "language",
    "level",
    "chunk_index",
}


class PostgresBatchWriter:
    """Atomically replace one active document and its chunks and embeddings.

    A writer deliberately exposes only one operation until PostgreSQL can pass
    the complete ``KnowledgeStore`` contract. Connections are supplied by the
    caller, so the same implementation works with a direct connection, pool,
    and deterministic test doubles.
    """

    def __init__(
        self,
        *,
        embedding_model: str,
        embedding_dim: int,
        tenant_id: str = "default",
        index_generation: int = 1,
        batch_size: int = 500,
    ) -> None:
        if not embedding_model.strip():
            raise ValueError("embedding_model must not be empty")
        if embedding_dim <= 0:
            raise ValueError("embedding_dim must be positive")
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if index_generation <= 0:
            raise ValueError("index_generation must be positive")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.tenant_id = tenant_id
        self.index_generation = index_generation
        self.batch_size = batch_size

    def replace_document(
        self,
        connection: Any,
        doc: DocumentRecord,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Any],
    ) -> int:
        """Replace ``doc`` and all its indexed data in one transaction.

        Active documents with the same tenant/collection/source are replaced;
        soft-deleted documents are retained. Chunk indices must be unique so
        returned PostgreSQL IDs can be paired with their input embeddings.
        """
        prepared_chunks, prepared_embeddings = self._prepare(doc, chunks, embeddings)

        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, tenant_id, deleted_at, document_version
                        FROM knowledge_documents
                        WHERE id = %s
                           OR (tenant_id = %s AND collection = %s AND source = %s
                               AND deleted_at IS NULL)
                        ORDER BY id
                        FOR UPDATE
                        """,
                        (doc.id, self.tenant_id, doc.collection, doc.source),
                    )
                    existing_rows = cursor.fetchall()
                    document_version = (
                        max(int(row[3]) for row in existing_rows) + 1 if existing_rows else 1
                    )
                    for existing in existing_rows:
                        if existing[0] == doc.id and existing[1] != self.tenant_id:
                            raise StoreError("document ID 已被其他 tenant 占用，拒绝跨租户覆盖")
                        if existing[0] == doc.id and existing[2] is not None:
                            raise StoreError("document ID 属于软删除文档；请使用新的 ID 重新摄入")

                    prepared_chunks = [
                        (row[0], document_version, *row[2:]) for row in prepared_chunks
                    ]

                    cursor.execute(
                        """
                        INSERT INTO knowledge_collections (name, tenant_id)
                        VALUES (%s, %s)
                        ON CONFLICT (tenant_id, name) DO NOTHING
                        """,
                        (doc.collection, self.tenant_id),
                    )
                    cursor.execute(
                        """
                        DELETE FROM knowledge_documents
                        WHERE tenant_id = %s AND deleted_at IS NULL
                          AND (id = %s OR (collection = %s AND source = %s))
                        """,
                        (self.tenant_id, doc.id, doc.collection, doc.source),
                    )
                    cursor.execute(
                        """
                        INSERT INTO knowledge_documents (
                            id, tenant_id, source, collection, format, file_hash,
                            size_bytes, page_count, chunk_count, created_at,
                            updated_at, title, tags, summary, enriched_at,
                            document_version, embedding_model, index_generation
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s::jsonb, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            tenant_id = EXCLUDED.tenant_id,
                            source = EXCLUDED.source,
                            collection = EXCLUDED.collection,
                            format = EXCLUDED.format,
                            file_hash = EXCLUDED.file_hash,
                            size_bytes = EXCLUDED.size_bytes,
                            page_count = EXCLUDED.page_count,
                            chunk_count = EXCLUDED.chunk_count,
                            updated_at = EXCLUDED.updated_at,
                            title = EXCLUDED.title,
                            tags = EXCLUDED.tags,
                            summary = EXCLUDED.summary,
                            enriched_at = EXCLUDED.enriched_at,
                            document_version = EXCLUDED.document_version,
                            embedding_model = EXCLUDED.embedding_model,
                            index_generation = EXCLUDED.index_generation
                        WHERE knowledge_documents.tenant_id = EXCLUDED.tenant_id
                          AND knowledge_documents.deleted_at IS NULL
                        RETURNING id
                        """,
                        (
                            doc.id,
                            self.tenant_id,
                            doc.source,
                            doc.collection,
                            doc.format,
                            doc.file_hash,
                            doc.size_bytes,
                            doc.page_count,
                            len(prepared_chunks),
                            doc.created_at,
                            doc.updated_at,
                            doc.title,
                            json.dumps(doc.tags, ensure_ascii=False)
                            if doc.tags is not None
                            else None,
                            doc.summary,
                            doc.enriched_at,
                            document_version,
                            self.embedding_model,
                            self.index_generation,
                        ),
                    )
                    if cursor.fetchone() is None:
                        raise StoreError("document ID 冲突，写入已取消以保护 tenant/软删除边界")

                    ids_by_index: dict[int, int] = {}
                    for start in range(0, len(prepared_chunks), self.batch_size):
                        batch = prepared_chunks[start : start + self.batch_size]
                        values_sql = ", ".join(
                            "(" + ", ".join(["%s"] * _CHUNK_ROW_WIDTH) + ")" for _ in batch
                        )
                        params = tuple(value for row in batch for value in row)
                        cursor.execute(
                            f"""
                            INSERT INTO knowledge_chunks ({_CHUNK_COLUMNS})
                            VALUES {values_sql}
                            RETURNING id, chunk_index
                            """,
                            params,
                        )
                        for chunk_id, chunk_index in cursor.fetchall():
                            ids_by_index[int(chunk_index)] = int(chunk_id)

                    expected_indices = {row[8] for row in prepared_chunks}
                    if set(ids_by_index) != expected_indices:
                        raise StoreError("PostgreSQL 返回的 chunk ID 与输入 chunk_index 不匹配")

                    embedding_rows = [
                        (
                            ids_by_index[index],
                            self.tenant_id,
                            self.embedding_model,
                            self.embedding_dim,
                            self.index_generation,
                            vector,
                        )
                        for index, vector in prepared_embeddings
                    ]
                    for start in range(0, len(embedding_rows), self.batch_size):
                        cursor.executemany(
                            """
                            INSERT INTO knowledge_chunk_embeddings (
                                chunk_id, tenant_id, embedding_model,
                                embedding_dim, index_generation, embedding
                            ) VALUES (%s, %s, %s, %s, %s, %s::vector)
                            """,
                            embedding_rows[start : start + self.batch_size],
                        )

                    cursor.execute(
                        """
                        UPDATE knowledge_documents
                        SET chunk_count = %s, updated_at = now()
                        WHERE id = %s AND tenant_id = %s
                        """,
                        (len(prepared_chunks), doc.id, self.tenant_id),
                    )
            return len(prepared_chunks)
        except StoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize driver errors at seam
            raise StoreError(f"PostgreSQL 文档批量替换失败: {exc}") from exc

    def _prepare(
        self,
        doc: DocumentRecord,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Any],
    ) -> tuple[list[tuple[Any, ...]], list[tuple[int, str]]]:
        if len(chunks) != len(embeddings):
            raise StoreError(f"chunks ({len(chunks)}) 与 embeddings ({len(embeddings)}) 长度不一致")

        prepared_chunks: list[tuple[Any, ...]] = []
        prepared_embeddings: list[tuple[int, str]] = []
        seen_indices: set[int] = set()
        for position, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            metadata = chunk.metadata
            try:
                chunk_index = int(metadata.get("chunk_index", position))
            except (TypeError, ValueError) as exc:
                raise StoreError(f"chunk_index 不是整数: {metadata.get('chunk_index')!r}") from exc
            if chunk_index < 0 or chunk_index in seen_indices:
                raise StoreError(f"chunk_index 必须非负且唯一: {chunk_index}")
            seen_indices.add(chunk_index)
            extra = {
                key: value for key, value in metadata.items() if key not in _KNOWN_CHUNK_METADATA
            }
            prepared_chunks.append(
                (
                    doc.id,
                    1,
                    doc.collection,
                    self.tenant_id,
                    doc.source,
                    doc.format,
                    chunk.content,
                    int(chunk.tokens),
                    chunk_index,
                    metadata.get("type"),
                    metadata.get("page"),
                    metadata.get("sheet"),
                    metadata.get("slide"),
                    metadata.get("heading"),
                    metadata.get("language"),
                    json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
                )
            )
            prepared_embeddings.append((chunk_index, self._vector_literal(embedding)))
        return prepared_chunks, prepared_embeddings

    def _vector_literal(self, embedding: Any) -> str:
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        if not isinstance(embedding, list | tuple):
            raise StoreError("embedding 必须是一维数值序列")
        if len(embedding) != self.embedding_dim:
            raise StoreError(f"embedding 维度 {len(embedding)} != 配置维度 {self.embedding_dim}")
        try:
            values = [float(value) for value in embedding]
        except (TypeError, ValueError) as exc:
            raise StoreError("embedding 包含非数值元素") from exc
        if not all(math.isfinite(value) for value in values):
            raise StoreError("embedding 不允许包含 NaN 或 Infinity")
        return "[" + ",".join(format(value, ".9g") for value in values) + "]"


def connect_postgres(dsn: str) -> Any:
    """Open a psycopg 3 connection without making PostgreSQL a Lite dependency."""
    try:
        import psycopg
    except ImportError as exc:
        raise StoreError("PostgreSQL 后端需要可选依赖；请安装 `doc2mind[postgres]`") from exc
    try:
        return psycopg.connect(dsn)
    except Exception as exc:  # noqa: BLE001 - normalize driver errors at seam
        raise StoreError(f"连接 PostgreSQL 失败: {exc}") from exc
