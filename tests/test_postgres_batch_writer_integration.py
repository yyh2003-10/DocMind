from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.store.postgres_write import PostgresBatchWriter, connect_postgres
from doc2mind.core.store.sqlite_vec import StoredDocument

pytestmark = pytest.mark.integration


def test_postgres_batch_writer_persists_document_chunks_and_vectors():
    dsn = os.environ.get("DOCMIND_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set DOCMIND_TEST_POSTGRES_DSN to an initialized PostgreSQL/pgvector test DB")

    identity = uuid4().hex
    tenant_id = f"writer-test-{identity}"
    collection = "batch-writer"
    document_id = f"writer-{identity}"
    source = f"{document_id}.md"
    now = datetime.now(timezone.utc).isoformat()
    doc = StoredDocument(
        id=document_id,
        source=source,
        collection=collection,
        format="md",
        file_hash=identity,
        size_bytes=20,
        page_count=None,
        chunk_count=2,
        created_at=now,
        updated_at=now,
    )
    connection = connect_postgres(dsn)
    try:
        count = PostgresBatchWriter(
            embedding_model="integration-test",
            embedding_dim=3,
            tenant_id=tenant_id,
            index_generation=1,
            batch_size=1,
        ).replace_document(
            connection,
            doc,
            [
                Chunk("first integration chunk", metadata={"chunk_index": 0}),
                Chunk("second integration chunk", metadata={"chunk_index": 1}),
            ],
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        )
        assert count == 2
        replacement_count = PostgresBatchWriter(
            embedding_model="integration-test",
            embedding_dim=3,
            tenant_id=tenant_id,
            index_generation=1,
            batch_size=1,
        ).replace_document(
            connection,
            doc,
            [Chunk("replacement chunk", metadata={"chunk_index": 0})],
            [[0.0, 0.0, 1.0]],
        )
        assert replacement_count == 1
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.chunk_count, d.document_version, c.chunk_index,
                       c.content, e.embedding::text
                FROM knowledge_documents AS d
                JOIN knowledge_chunks AS c ON c.document_id = d.id
                JOIN knowledge_chunk_embeddings AS e ON e.chunk_id = c.id
                WHERE d.id = %s AND d.tenant_id = %s
                ORDER BY c.chunk_index
                """,
                (document_id, tenant_id),
            )
            rows = cursor.fetchall()
        assert rows == [
            (1, 2, 0, "replacement chunk", "[0,0,1]"),
        ]
    finally:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM knowledge_documents WHERE id = %s AND tenant_id = %s",
                    (document_id, tenant_id),
                )
                cursor.execute(
                    "DELETE FROM knowledge_collections WHERE name = %s AND tenant_id = %s",
                    (collection, tenant_id),
                )
        connection.close()
