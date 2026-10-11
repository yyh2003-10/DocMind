"""PostgreSQL/pgvector implementation of the backend-neutral store seam.

The adapter owns connection lifecycle and query shaping; document replacement
delegates to :class:`PostgresBatchWriter` so the write invariants stay in one
deep module.  It deliberately exposes no psycopg types to callers.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.store.contracts import StoreCapabilities, StoreError, StoreHealth
from doc2mind.core.store.postgres_write import PostgresBatchWriter, connect_postgres
from doc2mind.core.store.sqlite_vec import StoredChunk, StoredDocument

_CHUNK_COLUMNS = (
    "document_id, document_version, collection, tenant_id, source, format, "
    "content, tokens, chunk_index, doc_type, page, sheet, slide, heading, "
    "language, extra"
)


def _vector_literal(value: Any, dimension: int) -> str:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, list | tuple) or len(value) != dimension:
        raise StoreError(
            f"embedding dimension {len(value) if isinstance(value, list | tuple) else '?'} != {dimension}"
        )
    try:
        values = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise StoreError("embedding must contain numeric values") from exc
    if not all(math.isfinite(item) for item in values):
        raise StoreError("embedding must not contain NaN or Infinity")
    return "[" + ",".join(format(item, ".9g") for item in values) + "]"


class PostgresKnowledgeStore:
    """Tenant-scoped PostgreSQL adapter satisfying ``KnowledgeStore``."""

    def __init__(
        self,
        dsn: str,
        embedding_dim: int,
        *,
        embedding_model: str = "",
        tenant_id: str = "default",
        index_generation: int = 1,
        connection: Any | None = None,
    ) -> None:
        if not dsn.strip() and connection is None:
            raise ValueError("dsn must not be empty")
        if embedding_dim <= 0:
            raise ValueError("embedding_dim must be positive")
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if index_generation <= 0:
            raise ValueError("index_generation must be positive")
        self.dsn = dsn
        self.embedding_dim = embedding_dim
        self.embedding_model = embedding_model.strip() or "unknown"
        self.tenant_id = tenant_id
        self.index_generation = index_generation
        self._connection = connection
        self._borrowed_connection = connection
        self._owns_connection = connection is None
        self._schema_ready = False

    @property
    def dimension(self) -> int:
        return self.embedding_dim

    @property
    def sparse_available(self) -> bool:
        return False

    @property
    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            backend="postgres",
            vector_search=True,
            lexical_search=True,
            sparse_search=False,
        )

    def open(self) -> None:
        if self._connection is None:
            if self._borrowed_connection is not None:
                self._connection = self._borrowed_connection
                self._owns_connection = False
            else:
                self._connection = connect_postgres(self.dsn)
                self._owns_connection = True
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM knowledge_schema_meta WHERE key = 'schema_version'")
            if cursor.fetchone() is None:
                raise StoreError("PostgreSQL schema is not initialized")
        rollback = getattr(self._connection, "rollback", None)
        if callable(rollback):
            rollback()
        self._schema_ready = True

    def close(self) -> None:
        if self._connection is not None and self._owns_connection:
            self._connection.close()
        # A borrowed connection remains owned by the caller, but this adapter
        # is closed and must not issue queries until explicitly reopened.
        self._connection = None
        self._schema_ready = False

    def health_check(self) -> StoreHealth:
        try:
            self._require_open()
            if not self._schema_ready:
                raise StoreError("PostgreSQL schema is not initialized")
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return StoreHealth(backend="postgres", healthy=True, detail="PostgreSQL/pgvector ready")
        except Exception as exc:  # noqa: BLE001
            return StoreHealth(backend="postgres", healthy=False, detail=str(exc))

    def vector_search(
        self, query_vec: Any, top_k: int = 10, collection: str | Sequence[str] | None = None
    ) -> list[tuple[int, float]]:
        self._require_open()
        clause, params = self._scope_clause(collection, "c")
        literal = _vector_literal(query_vec, self.embedding_dim)
        sql = f"""
            SELECT c.id, (e.embedding::vector({self.embedding_dim}) <=> %s::vector({self.embedding_dim})) AS distance
            FROM knowledge_chunks c
            JOIN knowledge_chunk_embeddings e ON e.chunk_id = c.id
            WHERE c.tenant_id = %s AND e.tenant_id = %s
              AND e.embedding_model = %s AND e.embedding_dim = %s
              AND e.index_generation = %s {clause}
            ORDER BY distance ASC LIMIT %s
        """
        return self._fetch_pairs(
            sql,
            (
                literal,
                self.tenant_id,
                self.tenant_id,
                self.embedding_model,
                self.embedding_dim,
                self.index_generation,
                *params,
                max(0, int(top_k)),
            ),
        )

    def bm25_search(
        self, query: str, top_k: int = 10, collection: str | Sequence[str] | None = None
    ) -> list[tuple[int, float]]:
        self._require_open()
        clause, params = self._scope_clause(collection, "c")
        sql = f"""
            SELECT c.id, ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s)) AS score
            FROM knowledge_chunks c
            WHERE c.tenant_id = %s AND c.search_vector @@ plainto_tsquery('simple', %s) {clause}
            ORDER BY score DESC, c.id ASC LIMIT %s
        """
        return self._fetch_pairs(sql, (query, self.tenant_id, query, *params, max(0, int(top_k))))

    def sparse_search(
        self, query: str, top_k: int = 10, collection: str | Sequence[str] | None = None
    ) -> list[tuple[int, float]]:
        return []

    def get_chunks(self, chunk_ids: Sequence[int]) -> list[StoredChunk]:
        self._require_open()
        ids = [int(item) for item in chunk_ids]
        if not ids:
            return []
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.id, c.content, c.source, c.format, c.doc_type, c.page,
                       c.heading, d.file_hash, c.collection, d.created_at,
                       c.tokens, c.chunk_index, c.extra
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE c.tenant_id = %s AND c.id = ANY(%s)
                """,
                (self.tenant_id, ids),
            )
            rows = {int(row[0]): row for row in cursor.fetchall()}
        return [self._stored_chunk(rows[item]) for item in ids if item in rows]

    def upsert_document(self, doc: StoredDocument) -> None:
        self._require_open()
        try:
            with self._connection.transaction():
                with self._connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO knowledge_collections (tenant_id, name, created_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (tenant_id, name) DO NOTHING
                        """,
                        (self.tenant_id, doc.collection, doc.created_at),
                    )
                    cursor.execute(
                        """
                    INSERT INTO knowledge_documents (
                        id, tenant_id, source, collection, format, file_hash,
                        size_bytes, page_count, chunk_count, created_at, updated_at,
                        title, tags, summary, enriched_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        source = EXCLUDED.source, collection = EXCLUDED.collection,
                        format = EXCLUDED.format, file_hash = EXCLUDED.file_hash,
                        size_bytes = EXCLUDED.size_bytes, page_count = EXCLUDED.page_count,
                        chunk_count = EXCLUDED.chunk_count, updated_at = EXCLUDED.updated_at,
                        title = EXCLUDED.title, tags = EXCLUDED.tags, summary = EXCLUDED.summary,
                        enriched_at = EXCLUDED.enriched_at
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
                            doc.chunk_count,
                            doc.created_at,
                            doc.updated_at,
                            doc.title,
                            json.dumps(doc.tags) if doc.tags is not None else None,
                            doc.summary,
                            doc.enriched_at,
                        ),
                    )
                    if cursor.fetchone() is None:
                        raise StoreError(
                            "document ID is owned by another tenant or a soft-deleted document"
                        )
        except StoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize driver errors at seam
            raise StoreError(f"PostgreSQL document upsert failed: {exc}") from exc

    def insert_chunks(
        self,
        document_id: str,
        collection: str,
        source: str,
        fmt: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Any],
    ) -> int:
        if len(chunks) != len(embeddings):
            raise StoreError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) length mismatch"
            )
        if not chunks:
            return 0
        doc = self.get_document_by_id(document_id)
        if doc is None:
            raise StoreError(f"document not found: {document_id}")
        if (doc.collection, doc.source, doc.format) != (collection, source, fmt):
            raise StoreError("chunk metadata does not match the stored document")
        writer = PostgresBatchWriter(
            embedding_model=self.embedding_model or "unknown",
            embedding_dim=self.embedding_dim,
            tenant_id=self.tenant_id,
            index_generation=self.index_generation,
        )
        prepared_chunks, prepared_embeddings = writer._prepare(doc, chunks, embeddings)
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT document_version, deleted_at FROM knowledge_documents WHERE id = %s AND tenant_id = %s",
                    (document_id, self.tenant_id),
                )
                version_row = cursor.fetchone()
                if version_row is None or version_row[1] is not None:
                    raise StoreError("document is missing or soft-deleted")
                document_version = int(version_row[0])
                rows = [(row[0], document_version, *row[2:]) for row in prepared_chunks]
                values_sql = ", ".join("(" + ", ".join(["%s"] * 16) + ")" for _ in rows)
                cursor.execute(
                    f"""
                    INSERT INTO knowledge_chunks ({_CHUNK_COLUMNS})
                    VALUES {values_sql}
                    RETURNING id, chunk_index
                    """,
                    tuple(value for row in rows for value in row),
                )
                ids_by_index = {int(index): int(chunk_id) for chunk_id, index in cursor.fetchall()}
                expected_indices = {int(row[8]) for row in rows}
                if set(ids_by_index) != expected_indices:
                    raise StoreError("PostgreSQL returned incomplete chunk IDs")
                embedding_map = dict(prepared_embeddings)
                embedding_rows = [
                    (
                        ids_by_index[index],
                        self.tenant_id,
                        self.embedding_model or "unknown",
                        self.embedding_dim,
                        self.index_generation,
                        embedding_map[index],
                    )
                    for index in sorted(ids_by_index)
                ]
                cursor.executemany(
                    """
                    INSERT INTO knowledge_chunk_embeddings (
                        chunk_id, tenant_id, embedding_model,
                        embedding_dim, index_generation, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s::vector)
                    """,
                    embedding_rows,
                )
                cursor.execute(
                    "UPDATE knowledge_documents SET chunk_count = chunk_count + %s, updated_at = now() WHERE id = %s AND tenant_id = %s",
                    (len(rows), document_id, self.tenant_id),
                )
        return len(rows)

    def replace_document(
        self, doc: StoredDocument, chunks: Sequence[Chunk], embeddings: Sequence[Any]
    ) -> int:
        self._require_open()
        return PostgresBatchWriter(
            embedding_model=self.embedding_model,
            embedding_dim=self.embedding_dim,
            tenant_id=self.tenant_id,
            index_generation=self.index_generation,
        ).replace_document(self._connection, doc, chunks, embeddings)

    def delete_document(self, document_id: str) -> int:
        self._require_open()
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT deleted_at FROM knowledge_documents WHERE id = %s AND tenant_id = %s",
                    (document_id, self.tenant_id),
                )
                row = cursor.fetchone()
                if row is None:
                    return -1
                if row[0] is not None:
                    return -2
                cursor.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = %s AND tenant_id = %s",
                    (document_id, self.tenant_id),
                )
                count_row = cursor.fetchone()
                chunk_count = int(count_row[0] or 0) if count_row else 0
                # Keep the document metadata for restore/audit, but physically
                # remove searchable chunks and vectors just like SQLite.
                cursor.execute(
                    "DELETE FROM knowledge_chunks WHERE document_id = %s AND tenant_id = %s",
                    (document_id, self.tenant_id),
                )
                cursor.execute(
                    "UPDATE knowledge_documents SET deleted_at = now(), chunk_count = 0, updated_at = now() WHERE id = %s AND tenant_id = %s",
                    (document_id, self.tenant_id),
                )
                return chunk_count

    def list_documents(
        self,
        collection: str | None = None,
        limit: int = 100,
        offset: int = 0,
        format: str | None = None,
        sort: str = "created_at_desc",
        q: str | None = None,
    ) -> list[StoredDocument]:
        self._require_open()
        clauses = ["tenant_id = %s", "deleted_at IS NULL"]
        params: list[Any] = [self.tenant_id]
        if collection:
            clauses.append("collection = %s")
            params.append(collection)
        if format:
            clauses.append("format = %s")
            params.append(format)
        if q:
            clauses.append("(source ILIKE %s OR title ILIKE %s OR summary ILIKE %s)")
            params.extend([f"%{q}%"] * 3)
        order = {
            "created_at_desc": "created_at DESC",
            "created_at_asc": "created_at ASC",
            "updated_at_desc": "updated_at DESC",
            "updated_at_asc": "updated_at ASC",
        }.get(sort, "created_at DESC")
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id, source, collection, format, file_hash, size_bytes, page_count, chunk_count, created_at, updated_at, title, tags, summary, enriched_at FROM knowledge_documents WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT %s OFFSET %s",
                (*params, max(0, int(limit)), max(0, int(offset))),
            )
            return [self._stored_document(row) for row in cursor.fetchall()]

    def find_document_id_by_hash(self, file_hash: str, collection: str) -> str | None:
        self._require_open()
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM knowledge_documents WHERE tenant_id = %s AND file_hash = %s AND collection = %s AND deleted_at IS NULL LIMIT 1",
                (self.tenant_id, file_hash, collection),
            )
            row = cursor.fetchone()
        return str(row[0]) if row else None

    def get_document_by_id(self, document_id: str) -> StoredDocument | None:
        self._require_open()
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, source, collection, format, file_hash, size_bytes, page_count, chunk_count, created_at, updated_at, title, tags, summary, enriched_at FROM knowledge_documents WHERE id = %s AND tenant_id = %s AND deleted_at IS NULL",
                (document_id, self.tenant_id),
            )
            row = cursor.fetchone()
        return self._stored_document(row) if row else None

    def list_chunks_by_document(self, document_id: str, limit: int = 100) -> list[StoredChunk]:
        self._require_open()
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.id, c.content, c.source, c.format, c.doc_type, c.page, c.heading, d.file_hash, c.collection, d.created_at, c.tokens, c.chunk_index, c.extra FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id WHERE c.tenant_id = %s AND c.document_id = %s ORDER BY c.chunk_index LIMIT %s",
                (self.tenant_id, document_id, max(0, int(limit))),
            )
            return [self._stored_chunk(row) for row in cursor.fetchall()]

    def list_chunk_contents(self, collection: str | None = None) -> list[tuple[int, str]]:
        self._require_open()
        clause, params = self._scope_clause(collection, "c")
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT c.id, c.content FROM knowledge_chunks c WHERE c.tenant_id = %s {clause} ORDER BY c.id",
                (self.tenant_id, *params),
            )
            return [(int(row[0]), str(row[1])) for row in cursor.fetchall()]

    def list_chunk_contexts(
        self, collection: str | None = None
    ) -> list[tuple[int, str, str | None]]:
        self._require_open()
        clause, params = self._scope_clause(collection, "c")
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT c.id, c.content, d.summary FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id WHERE c.tenant_id = %s {clause} ORDER BY c.id",
                (self.tenant_id, *params),
            )
            return [(int(row[0]), str(row[1]), row[2]) for row in cursor.fetchall()]

    def update_embeddings(self, chunk_id_emb_pairs: Sequence[tuple[int, object]]) -> int:
        self._require_open()
        if not chunk_id_emb_pairs:
            return 0
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                updated = 0
                for chunk_id, embedding in chunk_id_emb_pairs:
                    cursor.execute(
                        "UPDATE knowledge_chunk_embeddings SET embedding = %s::vector, embedding_dim = %s, index_generation = %s, embedding_model = %s WHERE chunk_id = %s AND tenant_id = %s",
                        (
                            _vector_literal(embedding, self.embedding_dim),
                            self.embedding_dim,
                            self.index_generation,
                            self.embedding_model,
                            chunk_id,
                            self.tenant_id,
                        ),
                    )
                    updated += max(0, int(cursor.rowcount))
                return updated

    def rebuild_chunk_embeddings(
        self, chunk_id_emb_pairs: Sequence[tuple[int, object]], new_dim: int
    ) -> int:
        if new_dim <= 0:
            raise StoreError("embedding dimension must be positive")
        if new_dim != self.embedding_dim:
            # The v1 schema deliberately stores an unconstrained pgvector. A
            # new generation can therefore be rebuilt in place; the caller is
            # still responsible for creating/validating the matching index.
            self.embedding_dim = int(new_dim)
            self.index_generation += 1
        return self.update_embeddings(chunk_id_emb_pairs)

    def purge_orphan_chunks(self) -> int:
        self._require_open()
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM knowledge_chunks c WHERE c.tenant_id = %s AND NOT EXISTS (SELECT 1 FROM knowledge_documents d WHERE d.id = c.document_id AND d.tenant_id = c.tenant_id)",
                    (self.tenant_id,),
                )
                return cursor.rowcount

    def _require_open(self) -> None:
        if self._connection is None:
            raise StoreError("PostgreSQL store is not open")

    def _scope_clause(
        self, collection: str | Sequence[str] | None, alias: str
    ) -> tuple[str, list[Any]]:
        if collection is None:
            return "", []
        values = [collection] if isinstance(collection, str) else list(collection)
        values = [value for value in values if isinstance(value, str) and value.strip()]
        if not values:
            return "", []
        return f"AND {alias}.collection = ANY(%s)", [values]

    def _fetch_pairs(self, sql: str, params: Sequence[Any]) -> list[tuple[int, float]]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [(int(row[0]), float(row[1])) for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            raise StoreError(f"PostgreSQL search failed: {exc}") from exc

    @staticmethod
    def _stored_document(row: Sequence[Any]) -> StoredDocument:
        tags = row[11]
        if isinstance(tags, str):
            tags = json.loads(tags)
        return StoredDocument(*row[:11], tags=tags, summary=row[12], enriched_at=row[13])

    @staticmethod
    def _stored_chunk(row: Sequence[Any]) -> StoredChunk:
        extra = row[12]
        if isinstance(extra, str):
            extra = json.loads(extra)
        return StoredChunk(
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7] or "",
            row[8],
            row[9],
            row[10],
            row[11],
            extra or {},
        )
