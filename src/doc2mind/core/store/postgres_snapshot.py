"""Transactional import of portable snapshots into PostgreSQL/pgvector."""

from __future__ import annotations

import json
import math
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from doc2mind.core.snapshot import SnapshotError, inspect_snapshot
from doc2mind.core.store.contracts import StoreError

_BATCH_SIZE = 500


def import_snapshot(
    connection: Any,
    snapshot_path: Path,
    *,
    tenant_id: str,
    embedding_model: str | None = None,
    index_generation: int = 1,
    batch_size: int = _BATCH_SIZE,
) -> dict[str, Any]:
    """Add a validated snapshot to one tenant in a single transaction.

    The importer never deletes or overwrites target records. Primary-key or
    active-source conflicts fail the transaction. Vector cache rows are only
    imported when the snapshot identifies their model and it matches the
    requested target model; otherwise they are skipped and ``needs_reindex``
    is true.
    """
    if not tenant_id.strip():
        raise ValueError("tenant_id must not be empty")
    if index_generation <= 0:
        raise ValueError("index_generation must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    try:
        manifest = inspect_snapshot(Path(snapshot_path))
        expected_counts = manifest.get("counts")
        if not isinstance(expected_counts, dict):
            raise SnapshotError("snapshot manifest 缺少 counts")
        count_keys = {"collections", "documents", "chunks", "embeddings"}
        if set(expected_counts) != count_keys:
            raise SnapshotError("snapshot manifest counts 必须且只能包含标准四项")
        for key in count_keys:
            if type(expected_counts.get(key)) is not int or expected_counts[key] < 0:
                raise SnapshotError(f"snapshot manifest counts.{key} 无效")

        embedding_info = manifest.get("embedding")
        source_model = embedding_info.get("model") if isinstance(embedding_info, dict) else None
        if source_model is not None and not isinstance(source_model, str):
            raise SnapshotError("snapshot embedding.model 必须是字符串或 null")
        if source_model is not None and not source_model.strip():
            raise SnapshotError("snapshot embedding.model 不能为空字符串")
        if embedding_model is not None and not embedding_model.strip():
            raise ValueError("embedding_model must not be empty")
        selected_model = embedding_model or source_model
        source_embedding_count = expected_counts["embeddings"]
        import_vectors = bool(
            source_embedding_count and source_model and selected_model == source_model
        )
        imported = dict.fromkeys(expected_counts, 0)

        with zipfile.ZipFile(snapshot_path) as archive:
            with connection.transaction():
                with connection.cursor() as cursor:
                    collection_names = _insert_collections(
                        cursor,
                        _jsonl_rows(archive, "collections.jsonl"),
                        tenant_id,
                        batch_size,
                        imported,
                    )
                    documents = _insert_documents(
                        cursor,
                        _jsonl_rows(archive, "documents.jsonl"),
                        tenant_id,
                        collection_names,
                        selected_model if import_vectors else None,
                        index_generation,
                        batch_size,
                        imported,
                    )
                    _insert_chunks_and_embeddings(
                        cursor,
                        _jsonl_rows(archive, "chunks.jsonl"),
                        _jsonl_rows(archive, "embeddings.jsonl"),
                        tenant_id=tenant_id,
                        embedding_model=source_model if import_vectors else None,
                        index_generation=index_generation,
                        manifest_embedding=embedding_info,
                        import_vectors=import_vectors,
                        batch_size=batch_size,
                        imported=imported,
                        documents=documents,
                    )
                    if imported != expected_counts:
                        raise SnapshotError(
                            "snapshot manifest counts 不匹配: "
                            f"expected={expected_counts}, actual={imported}"
                        )
                    # Imported chunk IDs are explicit. Advance the destination
                    # sequence so later DEFAULT inserts cannot reuse an ID.
                    cursor.execute(
                        """
                        SELECT setval(
                            'knowledge_chunk_id_seq',
                            GREATEST(
                                COALESCE((SELECT MAX(id) FROM knowledge_chunks), 1),
                                (SELECT last_value FROM knowledge_chunk_id_seq)
                            ),
                            true
                        )
                        """
                    )
        imported_vectors = imported["embeddings"] if import_vectors else 0
        return {
            "tenant_id": tenant_id,
            "counts": imported,
            "source_embeddings": source_embedding_count,
            "imported_embeddings": imported_vectors,
            "needs_reindex": imported_vectors != expected_counts["chunks"],
            "source_embedding_model": source_model,
            "target_embedding_model": selected_model,
            "index_generation": index_generation,
        }
    except SnapshotError:
        raise
    except StoreError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize driver/archive errors
        raise StoreError(f"PostgreSQL snapshot 导入失败: {exc}") from exc


def _jsonl_rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, Any]]:
    with archive.open(name, "r") as member:
        for line_number, raw_line in enumerate(member, start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
            except (ValueError, UnicodeDecodeError) as exc:
                raise SnapshotError(f"{name}:{line_number} JSONL 记录损坏") from exc
            if not isinstance(value, dict):
                raise SnapshotError(f"{name}:{line_number} 必须是 JSON 对象")
            yield value


def _batches(rows: Iterator[tuple[Any, ...]], size: int) -> Iterator[list[tuple[Any, ...]]]:
    batch: list[tuple[Any, ...]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _insert_collections(
    cursor: Any,
    rows: Iterator[dict[str, Any]],
    tenant_id: str,
    batch_size: int,
    imported: dict[str, int],
) -> set[str]:
    collection_names: set[str] = set()

    def values() -> Iterator[tuple[Any, ...]]:
        for row in rows:
            name = row.get("name")
            if not isinstance(name, str) or not name:
                raise SnapshotError("collection name 必须为非空字符串")
            if name in collection_names:
                raise SnapshotError(f"snapshot collection 重复: {name}")
            collection_names.add(name)
            yield (tenant_id, name, row.get("created_at"))
            imported["collections"] += 1

    for batch in _batches(values(), batch_size):
        cursor.executemany(
            """
            INSERT INTO knowledge_collections (tenant_id, name, created_at)
            VALUES (%s, %s, COALESCE(%s::timestamptz, now()))
            ON CONFLICT (tenant_id, name) DO NOTHING
            """,
            batch,
        )
    return collection_names


def _jsonb_parameter(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise SnapshotError(f"snapshot {field} JSON 损坏") from exc
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"snapshot {field} 无法序列化") from exc


def _insert_documents(
    cursor: Any,
    rows: Iterator[dict[str, Any]],
    tenant_id: str,
    collection_names: set[str],
    embedding_model: str | None,
    index_generation: int,
    batch_size: int,
    imported: dict[str, int],
) -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}

    def values() -> Iterator[tuple[Any, ...]]:
        for row in rows:
            required = ("id", "source", "collection", "format", "file_hash")
            if any(not isinstance(row.get(key), str) or not row[key] for key in required):
                raise SnapshotError("snapshot document 缺少必需的字符串字段")
            document_id = row["id"]
            if document_id in documents:
                raise SnapshotError(f"snapshot document ID 重复: {document_id}")
            if row["collection"] not in collection_names:
                raise SnapshotError(f"document {document_id} 引用未声明的 collection")
            try:
                size_bytes = int(row.get("size_bytes") or 0)
                chunk_count = int(row.get("chunk_count") or 0)
            except (TypeError, ValueError) as exc:
                raise SnapshotError("snapshot document 数值字段无效") from exc
            if size_bytes < 0 or chunk_count < 0:
                raise SnapshotError("snapshot document 数值字段不能为负")
            documents[document_id] = {
                "collection": row["collection"],
                "source": row["source"],
                "format": row["format"],
                "chunk_count": chunk_count,
                "actual_chunks": 0,
                "deleted_at": row.get("deleted_at"),
            }
            imported["documents"] += 1
            yield (
                document_id,
                tenant_id,
                row["source"],
                row["collection"],
                row["format"],
                row["file_hash"],
                size_bytes,
                row.get("page_count"),
                chunk_count,
                row.get("created_at"),
                row.get("updated_at"),
                row.get("title"),
                _jsonb_parameter(row.get("tags"), field="document.tags"),
                row.get("summary"),
                row.get("enriched_at"),
                row.get("deleted_at"),
                embedding_model,
                index_generation,
            )

    for batch in _batches(values(), batch_size):
        cursor.executemany(
            """
            INSERT INTO knowledge_documents (
                id, tenant_id, source, collection, format, file_hash,
                size_bytes, page_count, chunk_count, created_at, updated_at,
                title, tags, summary, enriched_at, deleted_at, document_version,
                embedding_model, index_generation
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                COALESCE(%s::timestamptz, now()), COALESCE(%s::timestamptz, now()),
                %s, %s::jsonb, %s, %s, %s::timestamptz, 1, %s, %s
            )
            """,
            batch,
        )
    return documents


def _embedding_value(row: dict[str, Any], manifest_embedding: Any) -> tuple[int, str, int]:
    if not isinstance(manifest_embedding, dict):
        raise SnapshotError("snapshot embeddings 存在但 manifest.embedding 缺失")
    try:
        chunk_id = int(row["chunk_id"])
        dimension = int(row["dimension"])
        values = row["values"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SnapshotError("snapshot embedding 字段无效") from exc
    if chunk_id <= 0 or dimension <= 0 or not isinstance(values, list):
        raise SnapshotError("snapshot embedding ID、维度或 values 无效")
    if dimension != manifest_embedding.get("dimension") or len(values) != dimension:
        raise SnapshotError(f"chunk {chunk_id} 向量维度与 manifest 不一致")
    dtype = row.get("dtype")
    if dtype not in {"float32", "int8"} or dtype != manifest_embedding.get("dtype"):
        raise SnapshotError(f"chunk {chunk_id} 向量 dtype 与 manifest 不一致")
    try:
        floats = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"chunk {chunk_id} 向量包含非数字") from exc
    if not all(math.isfinite(value) for value in floats):
        raise SnapshotError(f"chunk {chunk_id} 向量包含 NaN 或 Infinity")
    literal = "[" + ",".join(format(value, ".9g") for value in floats) + "]"
    return chunk_id, literal, dimension


def _insert_chunks_and_embeddings(
    cursor: Any,
    chunk_rows: Iterator[dict[str, Any]],
    embedding_rows: Iterator[dict[str, Any]],
    *,
    tenant_id: str,
    embedding_model: str | None,
    index_generation: int,
    manifest_embedding: Any,
    import_vectors: bool,
    batch_size: int,
    imported: dict[str, int],
    documents: dict[str, dict[str, Any]],
) -> None:
    chunk_batch: list[tuple[Any, ...]] = []
    vector_batch: list[tuple[Any, ...]] = []
    embedding_iterator = iter(embedding_rows)
    next_embedding = next(embedding_iterator, None)
    last_chunk_id = 0
    last_embedding_id = 0
    seen_chunk_keys: set[tuple[str, int, int]] = set()

    def flush() -> None:
        if chunk_batch:
            cursor.executemany(
                """
                INSERT INTO knowledge_chunks (
                    id, document_id, document_version, collection, tenant_id,
                    source, format, content, tokens, chunk_index, doc_type,
                    page, sheet, slide, heading, language, extra
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                """,
                chunk_batch,
            )
            chunk_batch.clear()
        if vector_batch:
            cursor.executemany(
                """
                INSERT INTO knowledge_chunk_embeddings (
                    chunk_id, tenant_id, embedding_model, embedding_dim,
                    index_generation, embedding
                ) VALUES (%s, %s, %s, %s, %s, %s::vector)
                """,
                vector_batch,
            )
            vector_batch.clear()

    for row in chunk_rows:
        try:
            chunk_id = int(row["id"])
            document_id = str(row["document_id"])
            chunk_index = int(row["chunk_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotError("snapshot chunk id/document_id/chunk_index 无效") from exc
        if (
            chunk_id <= last_chunk_id
            or not document_id
            or document_id not in documents
            or chunk_index < 0
        ):
            raise SnapshotError("snapshot chunks 必须按唯一递增 ID 排列，且字段有效")
        last_chunk_id = chunk_id

        try:
            document_version = int(row.get("document_version") or 1)
            tokens = int(row.get("tokens") or 0)
        except (TypeError, ValueError) as exc:
            raise SnapshotError("snapshot chunk 数值字段无效") from exc
        if document_version <= 0 or tokens < 0:
            raise SnapshotError("snapshot chunk 数值字段无效")
        chunk_key = (document_id, document_version, chunk_index)
        if chunk_key in seen_chunk_keys:
            raise SnapshotError("snapshot chunk_index 在同一文档版本内重复")
        seen_chunk_keys.add(chunk_key)
        for field in ("content", "collection", "source", "format"):
            if not isinstance(row.get(field), str):
                raise SnapshotError(f"snapshot chunk {field} 字段无效")
        document = documents[document_id]
        if document["deleted_at"] is not None:
            raise SnapshotError(f"snapshot deleted document {document_id} 不应包含 chunks")
        for field in ("collection", "source", "format"):
            if row[field] != document[field]:
                raise SnapshotError(f"snapshot chunk {field} 与 document {document_id} 不一致")
        document["actual_chunks"] += 1

        matched_embedding: tuple[int, str, int] | None = None
        if next_embedding is not None:
            try:
                embedding_id = int(next_embedding["chunk_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SnapshotError("snapshot embedding chunk_id 无效") from exc
            if embedding_id <= last_embedding_id:
                raise SnapshotError("snapshot embeddings 必须按唯一递增 chunk_id 排列")
            if embedding_id < chunk_id:
                raise SnapshotError(f"snapshot embedding 指向不存在的 chunk: {embedding_id}")
            if embedding_id == chunk_id:
                parsed = _embedding_value(next_embedding, manifest_embedding)
                matched_embedding = parsed
                last_embedding_id = embedding_id
                next_embedding = next(embedding_iterator, None)

        if matched_embedding and import_vectors:
            vector_batch.append(
                (
                    chunk_id,
                    tenant_id,
                    embedding_model,
                    matched_embedding[2],
                    index_generation,
                    matched_embedding[1],
                )
            )

        imported["chunks"] += 1
        if matched_embedding:
            imported["embeddings"] += 1

        extra = _jsonb_parameter(row.get("extra") or {}, field="chunk.extra") or "{}"
        chunk_batch.append(
            (
                chunk_id,
                document_id,
                document_version,
                row.get("collection"),
                tenant_id,
                row.get("source"),
                row.get("format"),
                row.get("content"),
                tokens,
                chunk_index,
                row.get("doc_type"),
                row.get("page"),
                row.get("sheet"),
                row.get("slide"),
                row.get("heading"),
                row.get("language"),
                extra,
            )
        )
        if len(chunk_batch) >= batch_size:
            flush()

    if next_embedding is not None:
        try:
            orphan_id = int(next_embedding["chunk_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotError("snapshot embedding chunk_id 无效") from exc
        raise SnapshotError(f"snapshot embedding 指向不存在的 chunk: {orphan_id}")
    for document_id, document in documents.items():
        if document["deleted_at"] is None and document["actual_chunks"] != document["chunk_count"]:
            raise SnapshotError(
                f"document {document_id} chunk_count 不匹配: "
                f"declared={document['chunk_count']}, actual={document['actual_chunks']}"
            )
    flush()
