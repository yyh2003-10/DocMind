from __future__ import annotations

from datetime import datetime, timezone

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.store.contracts import StoreError
from doc2mind.core.store.postgres_write import PostgresBatchWriter
from doc2mind.core.store.sqlite_vec import StoredDocument


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.started += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.connection.committed += 1
        else:
            self.connection.rolled_back += 1
        return False


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        self.connection.executed.append((normalized, params))
        self.rows = []
        if normalized.startswith("SELECT id, tenant_id, deleted_at"):
            self.rows = (
                [self.connection.existing_document] if self.connection.existing_document else []
            )
        elif normalized.startswith("INSERT INTO knowledge_documents"):
            self.rows = [(params[0],)]
        elif normalized.startswith("INSERT INTO knowledge_chunks"):
            indices = params[8::16]
            # Return reverse order to prove IDs are paired by chunk_index.
            self.rows = [
                (self.connection.next_chunk_id + offset, index)
                for offset, index in reversed(list(enumerate(indices)))
            ]
            self.connection.next_chunk_id += len(indices)
        if self.connection.fail_on_chunks and normalized.startswith("INSERT INTO knowledge_chunks"):
            raise RuntimeError("simulated chunk write failure")
        return self

    def executemany(self, query, params_seq):
        rows = list(params_seq)
        self.connection.executed_many.append((" ".join(query.split()), rows))
        if self.connection.fail_on_embeddings:
            raise RuntimeError("simulated embedding write failure")
        return self

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


class FakeConnection:
    def __init__(self, *, existing_document=None, fail_on_chunks=False, fail_on_embeddings=False):
        self.existing_document = existing_document
        self.fail_on_chunks = fail_on_chunks
        self.fail_on_embeddings = fail_on_embeddings
        self.next_chunk_id = 100
        self.started = 0
        self.committed = 0
        self.rolled_back = 0
        self.executed = []
        self.executed_many = []

    def transaction(self):
        return FakeTransaction(self)

    def cursor(self):
        return FakeCursor(self)


def _document() -> StoredDocument:
    now = datetime.now(timezone.utc).isoformat()
    return StoredDocument(
        id="pg-doc-1",
        source="guide.md",
        collection="engineering",
        format="md",
        file_hash="hash-1",
        size_bytes=120,
        page_count=None,
        chunk_count=3,
        created_at=now,
        updated_at=now,
        title="Guide",
        tags=["postgres", "storage"],
        summary="A test document",
    )


def _writer(*, batch_size=2) -> PostgresBatchWriter:
    return PostgresBatchWriter(
        embedding_model="test-embedding",
        embedding_dim=3,
        tenant_id="tenant-a",
        index_generation=7,
        batch_size=batch_size,
    )


def test_replace_document_batches_chunks_and_embeddings_in_one_transaction():
    connection = FakeConnection()
    chunks = [
        Chunk("first", tokens=1, metadata={"chunk_index": 4, "type": "paragraph"}),
        Chunk("second", tokens=2, metadata={"chunk_index": 9, "page": 2}),
        Chunk("third", tokens=3, metadata={"chunk_index": 12, "heading": "Next"}),
    ]
    embeddings = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

    inserted = _writer().replace_document(connection, _document(), chunks, embeddings)

    assert inserted == 3
    assert (connection.started, connection.committed, connection.rolled_back) == (1, 1, 0)
    chunk_statements = [
        (query, params)
        for query, params in connection.executed
        if query.startswith("INSERT INTO knowledge_chunks")
    ]
    assert len(chunk_statements) == 2
    first_rows = [chunk_statements[0][1][offset : offset + 16] for offset in range(0, 32, 16)]
    assert [row[0:9] for row in first_rows] == [
        ("pg-doc-1", 1, "engineering", "tenant-a", "guide.md", "md", "first", 1, 4),
        ("pg-doc-1", 1, "engineering", "tenant-a", "guide.md", "md", "second", 2, 9),
    ]
    embedding_batches = [rows for _, rows in connection.executed_many]
    assert [len(rows) for rows in embedding_batches] == [2, 1]
    embedded_by_id = {row[0]: row[5] for batch in embedding_batches for row in batch}
    assert embedded_by_id == {
        100: "[1,0,0]",
        101: "[0,1,0]",
        102: "[0,0,1]",
    }
    delete_query = next(
        query
        for query, _ in connection.executed
        if query.startswith("DELETE FROM knowledge_documents")
    )
    assert "deleted_at IS NULL" in delete_query


@pytest.mark.parametrize("failure", ["chunks", "embeddings"])
def test_replace_document_rolls_back_on_write_failure(failure):
    connection = FakeConnection(
        fail_on_chunks=failure == "chunks",
        fail_on_embeddings=failure == "embeddings",
    )
    with pytest.raises(StoreError, match="PostgreSQL 文档批量替换失败"):
        _writer().replace_document(
            connection,
            _document(),
            [Chunk("one"), Chunk("two"), Chunk("three")],
            [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        )
    assert connection.started == 1
    assert connection.committed == 0
    assert connection.rolled_back == 1


def test_replace_document_preserves_soft_deleted_document_id():
    connection = FakeConnection(
        existing_document=("pg-doc-1", "tenant-a", datetime.now(timezone.utc), 3)
    )
    with pytest.raises(StoreError, match="软删除"):
        _writer().replace_document(connection, _document(), [], [])
    assert connection.rolled_back == 1
    assert not any(
        query.startswith("DELETE FROM knowledge_documents") for query, _ in connection.executed
    )


def test_replace_document_increments_version_for_existing_active_id():
    connection = FakeConnection(existing_document=("pg-doc-1", "tenant-a", None, 4))

    inserted = _writer().replace_document(
        connection,
        _document(),
        [Chunk("revised", metadata={"chunk_index": 3})],
        [[1, 0, 0]],
    )

    assert inserted == 1
    doc_insert = next(
        params
        for query, params in connection.executed
        if query.startswith("INSERT INTO knowledge_documents")
    )
    assert doc_insert[15] == 5
    chunk_insert = next(
        params
        for query, params in connection.executed
        if query.startswith("INSERT INTO knowledge_chunks")
    )
    assert chunk_insert[1] == 5


def test_replace_document_increments_version_when_replacing_same_source_with_new_id():
    connection = FakeConnection(existing_document=("previous-doc", "tenant-a", None, 8))

    _writer().replace_document(
        connection,
        _document(),
        [Chunk("new source revision", metadata={"chunk_index": 0})],
        [[1, 0, 0]],
    )

    doc_insert = next(
        params
        for query, params in connection.executed
        if query.startswith("INSERT INTO knowledge_documents")
    )
    assert doc_insert[15] == 9


def test_replace_document_rejects_id_owned_by_another_tenant():
    connection = FakeConnection(existing_document=("pg-doc-1", "tenant-b", None, 1))

    with pytest.raises(StoreError, match="其他 tenant"):
        _writer().replace_document(connection, _document(), [], [])

    assert connection.rolled_back == 1
    assert not any(
        query.startswith("DELETE FROM knowledge_documents") for query, _ in connection.executed
    )


@pytest.mark.parametrize(
    ("chunks", "embeddings", "message"),
    [
        ([Chunk("one")], [], "长度不一致"),
        ([Chunk("one")], [[1, 0]], "维度"),
        ([Chunk("one")], [[1, float("nan"), 0]], "NaN"),
        (
            [Chunk("one", metadata={"chunk_index": 1}), Chunk("two", metadata={"chunk_index": 1})],
            [[1, 0, 0], [0, 1, 0]],
            "唯一",
        ),
    ],
)
def test_invalid_bundle_is_rejected_before_opening_transaction(chunks, embeddings, message):
    connection = FakeConnection()
    with pytest.raises(StoreError, match=message):
        _writer().replace_document(connection, _document(), chunks, embeddings)
    assert connection.started == 0
