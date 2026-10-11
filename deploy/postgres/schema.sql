-- DocMind Standard storage schema v1.
-- This script is backend groundwork only; the Python adapter will own
-- transaction boundaries and choose the concrete embedding dimension.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_schema_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO knowledge_schema_meta (key, value)
VALUES ('schema_version', '1')
ON CONFLICT (key) DO NOTHING;

CREATE SEQUENCE IF NOT EXISTS knowledge_chunk_id_seq;

CREATE TABLE IF NOT EXISTS knowledge_collections (
    tenant_id    TEXT NOT NULL DEFAULT 'default',
    name         TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL DEFAULT 'default',
    department_id     TEXT,
    source            TEXT NOT NULL,
    collection        TEXT NOT NULL,
    format            TEXT NOT NULL,
    file_hash         TEXT NOT NULL,
    document_version  INTEGER NOT NULL DEFAULT 1,
    embedding_model   TEXT,
    index_generation  BIGINT NOT NULL DEFAULT 0,
    size_bytes        BIGINT NOT NULL DEFAULT 0,
    page_count        INTEGER,
    chunk_count       INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    title             TEXT,
    tags              JSONB,
    summary           TEXT,
    enriched_at       TIMESTAMPTZ,
    deleted_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_scope
    ON knowledge_documents (tenant_id, collection, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_hash
    ON knowledge_documents (tenant_id, file_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_documents_active_source
    ON knowledge_documents (tenant_id, collection, source)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id             BIGINT PRIMARY KEY DEFAULT nextval('knowledge_chunk_id_seq'),
    document_id    TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    document_version INTEGER NOT NULL DEFAULT 1,
    collection     TEXT NOT NULL,
    tenant_id      TEXT NOT NULL DEFAULT 'default',
    source         TEXT NOT NULL,
    format         TEXT NOT NULL,
    content        TEXT NOT NULL,
    tokens         INTEGER NOT NULL DEFAULT 0,
    chunk_index    INTEGER NOT NULL,
    doc_type       TEXT,
    page           INTEGER,
    sheet          TEXT,
    slide          INTEGER,
    heading        TEXT,
    language       TEXT,
    extra          JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector  TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(content, ''))
    ) STORED,
    UNIQUE (document_id, document_version, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_scope
    ON knowledge_chunks (tenant_id, collection, document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_search
    ON knowledge_chunks USING GIN (search_vector);

-- Dimension is intentionally unconstrained in v1 so one schema can receive a
-- snapshot before the deployment selects its embedding model. The adapter
-- creates a dimension-specific HNSW index after validating the active model.
CREATE TABLE IF NOT EXISTS knowledge_chunk_embeddings (
    chunk_id          BIGINT PRIMARY KEY REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
    tenant_id         TEXT NOT NULL DEFAULT 'default',
    embedding_model   TEXT NOT NULL,
    embedding_dim     INTEGER NOT NULL CHECK (embedding_dim > 0),
    index_generation  BIGINT NOT NULL,
    embedding         vector NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_generation
    ON knowledge_chunk_embeddings (tenant_id, embedding_model, index_generation);

CREATE TABLE IF NOT EXISTS knowledge_jobs (
    id             TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL DEFAULT 'default',
    kind           TEXT NOT NULL,
    status         TEXT NOT NULL,
    payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    result         JSONB,
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_knowledge_jobs_status
    ON knowledge_jobs (tenant_id, status, created_at DESC);
