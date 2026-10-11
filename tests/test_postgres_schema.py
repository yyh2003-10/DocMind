from __future__ import annotations

from pathlib import Path


def test_postgres_schema_declares_migration_contract():
    schema = (Path(__file__).parents[1] / "deploy/postgres/schema.sql").read_text(encoding="utf-8")
    required = (
        "CREATE EXTENSION IF NOT EXISTS vector",
        "knowledge_schema_meta",
        "knowledge_documents",
        "knowledge_chunks",
        "knowledge_chunk_embeddings",
        "knowledge_jobs",
        "PRIMARY KEY (tenant_id, name)",
        "document_version",
        "embedding_model",
        "index_generation",
        "deleted_at",
        "USING GIN (search_vector)",
    )
    for fragment in required:
        assert fragment in schema
    assert "CREATE TABLE IF NOT EXISTS sqlite" not in schema
