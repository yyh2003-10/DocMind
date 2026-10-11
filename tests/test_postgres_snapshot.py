from __future__ import annotations

import hashlib
import json
import os
import zipfile
from uuid import uuid4

import pytest

from doc2mind.core.snapshot import SnapshotError
from doc2mind.core.store.contracts import StoreError
from doc2mind.core.store.postgres_snapshot import import_snapshot
from doc2mind.core.store.postgres_write import connect_postgres

_MEMBERS = (
    "collections.jsonl",
    "documents.jsonl",
    "chunks.jsonl",
    "embeddings.jsonl",
)


def _snapshot(
    path,
    *,
    model="embed-v1",
    dtype="float32",
    counts_override=None,
    identity="sample",
    first_chunk_id=10,
):
    document_id = f"doc-{identity}"
    collection = f"engineering-{identity}"
    records = {
        "collections.jsonl": [{"name": collection, "created_at": "2026-10-09T00:00:00Z"}],
        "documents.jsonl": [
            {
                "id": document_id,
                "source": "guide.md",
                "collection": collection,
                "format": "md",
                "file_hash": "hash-1",
                "size_bytes": 120,
                "page_count": None,
                "chunk_count": 2,
                "created_at": "2026-10-09T00:00:00Z",
                "updated_at": "2026-10-09T00:00:00Z",
                "title": "Guide",
                "tags": '["storage"]',
                "summary": "A guide",
                "enriched_at": None,
                "deleted_at": None,
            }
        ],
        "chunks.jsonl": [
            {
                "id": first_chunk_id,
                "document_id": document_id,
                "content": "first chunk",
                "tokens": 2,
                "chunk_index": 0,
                "collection": collection,
                "source": "guide.md",
                "format": "md",
                "doc_type": "paragraph",
                "page": None,
                "sheet": None,
                "slide": None,
                "heading": None,
                "language": "en",
                "extra": "{}",
            },
            {
                "id": first_chunk_id + 1,
                "document_id": document_id,
                "content": "second chunk",
                "tokens": 2,
                "chunk_index": 1,
                "collection": collection,
                "source": "guide.md",
                "format": "md",
                "doc_type": "paragraph",
                "page": None,
                "sheet": None,
                "slide": None,
                "heading": "Next",
                "language": "en",
                "extra": '{"source_format":"md"}',
            },
        ],
        "embeddings.jsonl": [
            {"chunk_id": first_chunk_id, "dtype": dtype, "dimension": 3, "values": [1, 0, 0]},
            {
                "chunk_id": first_chunk_id + 1,
                "dtype": dtype,
                "dimension": 3,
                "values": [0, 1, 0],
            },
        ],
    }
    data = {
        name: "".join(
            json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
            for row in records[name]
        ).encode()
        for name in _MEMBERS
    }
    counts = {name.removesuffix(".jsonl"): len(rows) for name, rows in records.items()}
    counts["embeddings"] = counts.pop("embeddings")
    if counts_override:
        counts.update(counts_override)
    manifest = {
        "format": "docmind-snapshot",
        "version": 1,
        "embedding": {"dtype": dtype, "dimension": 3, "model": model},
        "counts": counts,
        "members": {
            name: {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for name, value in data.items()
        },
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, value in data.items():
            archive.writestr(name, value)
    return path


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.started += 1

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.connection.committed += 1
        else:
            self.connection.rolled_back += 1
        return False


class _Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def executemany(self, query, rows):
        batch = list(rows)
        self.connection.calls.append((" ".join(query.split()), batch))
        if self.connection.fail:
            raise RuntimeError("simulated database constraint failure")

    def execute(self, query, *params):
        self.connection.calls.append((" ".join(query.split()), list(params)))
        if self.connection.fail:
            raise RuntimeError("simulated database constraint failure")


class _Connection:
    def __init__(self, fail=False):
        self.fail = fail
        self.started = 0
        self.committed = 0
        self.rolled_back = 0
        self.calls = []

    def transaction(self):
        return _Transaction(self)

    def cursor(self):
        return _Cursor(self)


def test_import_snapshot_preserves_ids_and_batches_rows(tmp_path):
    path = _snapshot(tmp_path / "sample.snapshot.zip")
    connection = _Connection()

    report = import_snapshot(
        connection,
        path,
        tenant_id="tenant-a",
        embedding_model="embed-v1",
        batch_size=1,
    )

    assert report["counts"] == {
        "collections": 1,
        "documents": 1,
        "chunks": 2,
        "embeddings": 2,
    }
    assert report["imported_embeddings"] == 2
    assert report["needs_reindex"] is False
    assert connection.started == connection.committed == 1
    assert connection.rolled_back == 0
    chunk_calls = [
        (query, rows) for query, rows in connection.calls if "knowledge_chunks (" in query
    ]
    assert [rows[0][0] for _, rows in chunk_calls] == [10, 11]
    embedding_calls = [
        rows for query, rows in connection.calls if "knowledge_chunk_embeddings" in query
    ]
    assert [row[0] for batch in embedding_calls for row in batch] == [10, 11]
    assert [row[5] for batch in embedding_calls for row in batch] == ["[1,0,0]", "[0,1,0]"]


def test_import_snapshot_skips_vector_cache_when_model_does_not_match(tmp_path):
    path = _snapshot(tmp_path / "sample.snapshot.zip")
    connection = _Connection()

    report = import_snapshot(
        connection,
        path,
        tenant_id="tenant-a",
        embedding_model="embed-v2",
    )

    assert report["source_embeddings"] == 2
    assert report["imported_embeddings"] == 0
    assert report["needs_reindex"] is True
    assert not any("knowledge_chunk_embeddings" in query for query, _ in connection.calls)


def test_import_snapshot_imports_quantized_vector_cache_as_numeric_vectors(tmp_path):
    path = _snapshot(tmp_path / "quantized.snapshot.zip", dtype="int8")
    connection = _Connection()

    report = import_snapshot(connection, path, tenant_id="tenant-a", embedding_model="embed-v1")

    assert report["source_embeddings"] == 2
    assert report["imported_embeddings"] == 2
    assert report["needs_reindex"] is False
    assert any("knowledge_chunk_embeddings" in query for query, _ in connection.calls)


def test_import_snapshot_rolls_back_on_database_failure(tmp_path):
    path = _snapshot(tmp_path / "sample.snapshot.zip")
    connection = _Connection(fail=True)

    with pytest.raises(StoreError, match="snapshot 导入失败"):
        import_snapshot(connection, path, tenant_id="tenant-a", embedding_model="embed-v1")

    assert connection.started == 1
    assert connection.committed == 0
    assert connection.rolled_back == 1


def test_import_snapshot_checks_counts_before_commit(tmp_path):
    path = _snapshot(
        tmp_path / "bad-counts.snapshot.zip",
        counts_override={"chunks": 99},
    )
    connection = _Connection()

    with pytest.raises(SnapshotError, match="counts 不匹配"):
        import_snapshot(connection, path, tenant_id="tenant-a", embedding_model="embed-v1")

    assert connection.committed == 0
    assert connection.rolled_back == 1


def test_import_snapshot_advances_chunk_sequence_inside_transaction(tmp_path):
    path = _snapshot(tmp_path / "sample.snapshot.zip")
    connection = _Connection()

    import_snapshot(connection, path, tenant_id="tenant-a", embedding_model="embed-v1")

    assert any("setval" in query for query, _ in connection.calls)


def test_import_snapshot_rejects_chunk_for_unknown_document(tmp_path):
    path = _snapshot(tmp_path / "bad-reference.snapshot.zip")
    with zipfile.ZipFile(path, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}
    chunks = json.loads(members["chunks.jsonl"].splitlines()[0])
    chunks["document_id"] = "missing-document"
    members["chunks.jsonl"] = (
        json.dumps(chunks, separators=(",", ":")).encode()
        + b"\n"
        + members["chunks.jsonl"].splitlines()[1]
        + b"\n"
    )
    _rewrite_snapshot(path, members)

    connection = _Connection()
    with pytest.raises(SnapshotError, match="snapshot chunks"):
        import_snapshot(connection, path, tenant_id="tenant-a", embedding_model="embed-v1")
    assert connection.committed == 0
    assert connection.rolled_back == 1


def test_import_snapshot_rejects_document_chunk_count_mismatch(tmp_path):
    path = _snapshot(tmp_path / "bad-document-count.snapshot.zip")
    with zipfile.ZipFile(path, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}
    documents = json.loads(members["documents.jsonl"])
    documents["chunk_count"] = 3
    members["documents.jsonl"] = json.dumps(documents, separators=(",", ":")).encode() + b"\n"
    _rewrite_snapshot(path, members)

    connection = _Connection()
    with pytest.raises(SnapshotError, match="chunk_count"):
        import_snapshot(connection, path, tenant_id="tenant-a", embedding_model="embed-v1")
    assert connection.committed == 0
    assert connection.rolled_back == 1


def _rewrite_snapshot(path, members):
    manifest = json.loads(members.pop("manifest.json")) if "manifest.json" in members else None
    if manifest is None:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
    manifest["members"] = {
        name: {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        for name, value in members.items()
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, value in members.items():
            archive.writestr(name, value)


@pytest.mark.integration
def test_postgres_snapshot_import_preserves_ids_and_advances_sequence(tmp_path):
    dsn = os.environ.get("DOCMIND_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set DOCMIND_TEST_POSTGRES_DSN to an initialized PostgreSQL/pgvector test DB")

    identity = uuid4().hex
    tenant_id = f"snapshot-test-{identity}"
    connection = connect_postgres(dsn)
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('knowledge_chunk_id_seq')")
        cursor.fetchone()
        cursor.execute("SELECT nextval('knowledge_chunk_id_seq')")
        reserved_id = cursor.fetchone()[0]
    connection.commit()
    first_chunk_id = reserved_id + 1
    path = _snapshot(
        tmp_path / "integration.snapshot.zip",
        identity=identity,
        first_chunk_id=first_chunk_id,
    )
    generated_document_id = f"generated-{identity}"
    generated_source = f"generated-{identity}.md"
    collection = f"engineering-{identity}"
    try:
        report = import_snapshot(
            connection,
            path,
            tenant_id=tenant_id,
            embedding_model="embed-v1",
        )
        assert report["counts"]["chunks"] == 2
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id FROM knowledge_chunks
                    WHERE tenant_id = %s AND id = %s
                    """,
                    (tenant_id, first_chunk_id),
                )
                assert cursor.fetchone() == (first_chunk_id,)
                cursor.execute(
                    """
                    INSERT INTO knowledge_documents (
                        id, tenant_id, source, collection, format, file_hash,
                        size_bytes, chunk_count
                    ) VALUES (%s, %s, %s, %s, 'md', %s, 1, 1)
                    """,
                    (generated_document_id, tenant_id, generated_source, collection, identity),
                )
                cursor.execute(
                    """
                    INSERT INTO knowledge_chunks (
                        document_id, collection, tenant_id, source, format,
                        content, chunk_index
                    ) VALUES (%s, %s, %s, %s, 'md', 'sequence check', 0)
                    RETURNING id
                    """,
                    (generated_document_id, collection, tenant_id, generated_source),
                )
                generated_chunk_id = cursor.fetchone()[0]
                assert generated_chunk_id > first_chunk_id + 1
    finally:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM knowledge_documents WHERE tenant_id = %s AND id IN (%s, %s)",
                    (tenant_id, f"doc-{identity}", generated_document_id),
                )
                cursor.execute(
                    "DELETE FROM knowledge_collections WHERE tenant_id = %s AND name = %s",
                    (tenant_id, collection),
                )
        connection.close()
