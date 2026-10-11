from __future__ import annotations

from datetime import datetime, timezone

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.retriever.search import Retriever
from doc2mind.core.store import (
    InMemoryJobStore,
    JobStore,
    KnowledgeStore,
    SqliteKnowledgeStore,
    StoreError,
    VectorStore,
    create_knowledge_store,
)
from doc2mind.core.store.sqlite_vec import StoredDocument

EMBEDDING_DIM = 4


def test_job_store_contract_and_terminal_pruning():
    from types import SimpleNamespace

    store = InMemoryJobStore()
    assert isinstance(store, JobStore)

    store["active"] = SimpleNamespace(
        status="running", started_at="2026-10-08T00:00:00+00:00", finished_at=None
    )
    store["old"] = SimpleNamespace(
        status="completed", started_at="2020-01-01T00:00:00+00:00",
        finished_at="2020-01-01T00:00:01+00:00",
    )
    store["new"] = SimpleNamespace(
        status="completed", started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
    )

    removed = store.prune(
        terminal_statuses=("completed", "failed", "cancelled"),
        ttl_seconds=3600,
        max_keep=2,
    )
    assert removed == ["old"]
    assert set(store) == {"active", "new"}


@pytest.fixture
def contract_store(tmp_path):
    """Adapter fixture: add future implementations here to run shared contracts."""

    class Settings:
        storage_backend = "sqlite"
        deployment_profile = "lite"
        db_path = tmp_path / "knowledge-store-contract.db"
        bm25_jieba_enabled = False
        sparse_retrieval_enabled = False
        vector_quantize = "none"

    store = create_knowledge_store(Settings(), embedding_dim=EMBEDDING_DIM)
    try:
        store.open()
    except Exception as exc:  # noqa: BLE001 — optional native extension
        pytest.skip(f"SQLite vector backend unavailable: {exc}")
    try:
        yield store
    finally:
        store.close()


def test_sqlite_store_implements_knowledge_store(tmp_path):
    store = SqliteKnowledgeStore(tmp_path / "contract.db", embedding_dim=4)
    try:
        assert isinstance(store, KnowledgeStore)
        assert VectorStore is SqliteKnowledgeStore
        assert Retriever.__init__.__annotations__["store"] == "KnowledgeStore"
        store.open()
        assert store.capabilities.backend == "sqlite"
        assert store.capabilities.vector_search is True
        assert store.capabilities.lexical_search is True
        assert store.capabilities.sparse_search is False
        assert store.health_check().healthy is True
    finally:
        store.close()
    assert store.health_check().healthy is False


def test_store_factory_uses_sqlite_settings(tmp_path):
    class Settings:
        storage_backend = "sqlite"
        db_path = tmp_path / "factory.db"
        bm25_jieba_enabled = True
        sparse_retrieval_enabled = True
        vector_quantize = "int8"

    store = create_knowledge_store(Settings(), embedding_dim=4)
    try:
        assert isinstance(store, SqliteKnowledgeStore)
        assert store.db_path == Settings.db_path
        assert store.bm25_jieba_enabled is True
        assert store.sparse_retrieval_enabled is True
        assert store.vector_quantize == "int8"
    finally:
        store.close()


def test_store_factory_requires_postgres_configuration(tmp_path):
    class Settings:
        storage_backend = "postgres"
        deployment_profile = "standard"
        db_path = tmp_path / "must-not-fallback.db"

    try:
        create_knowledge_store(Settings(), embedding_dim=4)
    except StoreError as exc:
        assert "postgres" in str(exc).lower()
    else:
        raise AssertionError("PostgreSQL backend without DSN must fail explicitly")


def test_store_factory_rejects_unimplemented_deployment_profile(tmp_path):
    class Settings:
        storage_backend = "sqlite"
        deployment_profile = "standard"
        db_path = tmp_path / "must-not-fallback.db"

    try:
        create_knowledge_store(Settings(), embedding_dim=4)
    except StoreError as exc:
        assert "部署档位" in str(exc)
    else:
        raise AssertionError("unsupported deployment profile silently selected Lite")


def test_knowledge_store_write_list_and_batch_read_contract(contract_store):
    doc = StoredDocument(
        id="contract-doc",
        source="contract.md",
        collection="contract",
        format="md",
        file_hash="contract-hash",
        size_bytes=42,
        page_count=None,
        chunk_count=2,
        created_at="2026-10-08T00:00:00+08:00",
        updated_at="2026-10-08T00:00:00+08:00",
    )
    contract_store.upsert_document(doc)
    inserted = contract_store.insert_chunks(
        document_id=doc.id,
        collection=doc.collection,
        source=doc.source,
        fmt=doc.format,
        chunks=[
            Chunk("vector knowledge retrieval contract", tokens=4),
            Chunk("lexical indexing behavior contract", tokens=4),
        ],
        embeddings=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
    )

    documents = contract_store.list_documents(collection="contract")
    lexical_hits = contract_store.bm25_search("retrieval contract", 10, "contract")
    chunks = contract_store.get_chunks([chunk_id for chunk_id, _ in lexical_hits])
    assert inserted == 2
    assert [item.id for item in documents] == [doc.id]
    assert len(chunks) == 2
    assert {item.source for item in chunks} == {doc.source}


def test_knowledge_store_search_contract_honors_collection_filter(contract_store):
    docs = [
        StoredDocument(
            id=f"search-{collection}",
            source=f"{collection}.md",
            collection=collection,
            format="md",
            file_hash=f"hash-{collection}",
            size_bytes=10,
            page_count=None,
            chunk_count=1,
            created_at="2026-10-08T00:00:00+08:00",
            updated_at="2026-10-08T00:00:00+08:00",
        )
        for collection in ("alpha", "beta")
    ]
    for doc, vector in zip(
        docs,
        ([1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]),
        strict=True,
    ):
        contract_store.upsert_document(doc)
        contract_store.insert_chunks(
            doc.id,
            doc.collection,
            doc.source,
            doc.format,
            [Chunk("shared retrieval contract keyword", tokens=4)],
            [vector],
        )

    vector_hits = contract_store.vector_search(
        [1.0, 0.0, 0.0, 0.0], top_k=10, collection="alpha"
    )
    lexical_hits = contract_store.bm25_search(
        "retrieval contract keyword", top_k=10, collection="beta"
    )
    assert len(vector_hits) == 1
    assert len(lexical_hits) == 1
    assert {chunk.collection for chunk in contract_store.get_chunks(
        [vector_hits[0][0], lexical_hits[0][0]]
    )} == {"alpha", "beta"}


def test_knowledge_store_delete_contract_removes_active_document(contract_store):
    doc = StoredDocument(
        id="delete-contract-doc",
        source="delete-contract.md",
        collection="contract",
        format="md",
        file_hash="delete-contract-hash",
        size_bytes=10,
        page_count=None,
        chunk_count=1,
        created_at="2026-10-08T00:00:00+08:00",
        updated_at="2026-10-08T00:00:00+08:00",
    )
    contract_store.upsert_document(doc)
    contract_store.insert_chunks(
        doc.id,
        doc.collection,
        doc.source,
        doc.format,
        [Chunk("deletable contract content", tokens=3)],
        [[1.0, 0.0, 0.0, 0.0]],
    )

    deleted_chunks = contract_store.delete_document(doc.id)
    assert deleted_chunks == 1
    assert contract_store.list_documents(collection="contract") == []
