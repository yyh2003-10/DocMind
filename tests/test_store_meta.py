"""documents 表 AI 整理元数据（curate）的存储层测试。

覆盖：
- 旧库 schema 自动迁移（幂等 ALTER TABLE 补列）
- update_document_meta 部分更新与回读（tags JSON 往返）
- move_document 跨集合移动后 documents / chunks_meta / 向量 / BM25 一致
- UNIQUE(collection, source) 冲突时 move 报 StoreError

sqlite-vec 为可选本地扩展，不可用时跳过（与生产降级行为一致）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import numpy as np
import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.store.sqlite_vec import StoredDocument, StoreError, VectorStore

EMBEDDING_DIM = 8

_OLD_DOCUMENTS_DDL = """
CREATE TABLE documents (
    id            TEXT    PRIMARY KEY,
    source        TEXT    NOT NULL,
    collection    TEXT    NOT NULL DEFAULT 'default',
    format        TEXT    NOT NULL,
    file_hash     TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    page_count    INTEGER,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    UNIQUE (collection, source)
);
CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
CREATE INDEX IF NOT EXISTS idx_documents_hash      ON documents(file_hash);
"""


def _doc(doc_id: str, collection: str, source: str, chunks: int = 1) -> StoredDocument:
    return StoredDocument(
        id=doc_id,
        source=source,
        collection=collection,
        format="md",
        file_hash=f"hash-{doc_id}",
        size_bytes=100,
        page_count=None,
        chunk_count=chunks,
        created_at="2026-01-01T00:00:00+08:00",
        updated_at="2026-01-01T00:00:00+08:00",
    )


def _chunk(content: str) -> Chunk:
    return Chunk(content=content, tokens=len(content), metadata={"chunk_index": 0})


def _embed(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).random(EMBEDDING_DIM, dtype=np.float32)


def _open_store(db_path) -> Any:
    vs = VectorStore(db_path, embedding_dim=EMBEDDING_DIM)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001 — sqlite-vec 缺失则跳过
        pytest.skip(f"sqlite-vec 不可用: {e}")
    return vs


@pytest.fixture()
def store(tmp_path) -> Any:
    vs = _open_store(tmp_path / "test.db")
    vs.upsert_document(_doc("d-1", "default", "note:第一条", 1))
    vs.insert_chunks(
        document_id="d-1", collection="default", source="note:第一条",
        fmt="md", chunks=[_chunk("alpha electron content")], embeddings=[_embed(1)],
    )
    yield vs
    vs.close()


class TestSchemaMigration:
    def test_old_db_gets_meta_columns(self, tmp_path) -> None:
        """旧 schema 的库打开后自动补齐 title/tags/summary/enriched_at。"""
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript(_OLD_DOCUMENTS_DDL)
        conn.execute(
            "INSERT INTO documents (id, source, collection, format, file_hash,"
            " size_bytes, chunk_count, created_at, updated_at)"
            " VALUES ('old-1', 'old.txt', 'default', 'txt', 'h1', 10, 0,"
            " '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        vs = _open_store(db)
        try:
            cols = {
                row[1] for row in vs._conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            for expected in ("title", "tags", "summary", "enriched_at"):
                assert expected in cols
            # 旧数据仍在
            doc = vs.get_document_by_id("old-1")
            assert doc is not None and doc.source == "old.txt"
            assert doc.title is None and doc.tags is None and doc.summary is None
        finally:
            vs.close()

    def test_migration_idempotent(self, tmp_path) -> None:
        """重复打开不重复加列（第二次 open 为 no-op）。"""
        db = tmp_path / "old2.db"
        conn = sqlite3.connect(db)
        conn.executescript(_OLD_DOCUMENTS_DDL)
        conn.commit()
        conn.close()

        vs = _open_store(db)
        vs.close()
        vs2 = _open_store(db)
        try:
            cols = [
                row[1] for row in vs2._conn.execute("PRAGMA table_info(documents)").fetchall()
            ]
            assert cols.count("title") == 1
            assert cols.count("tags") == 1
        finally:
            vs2.close()

    def test_new_db_has_columns_in_list(self, store) -> None:
        docs = store.list_documents(collection="default")
        assert len(docs) == 1
        assert docs[0].title is None
        assert docs[0].tags is None
        assert docs[0].summary is None
        assert docs[0].enriched_at is None


class TestUpdateDocumentMeta:
    def test_write_and_readback(self, store) -> None:
        ok = store.update_document_meta(
            "d-1", title="标题甲", tags=["bug", "sqlite"], summary="一句话摘要",
            enriched_at="2026-08-16T00:00:00+08:00",
        )
        assert ok is True
        doc = store.get_document_by_id("d-1")
        assert doc is not None
        assert doc.title == "标题甲"
        assert doc.tags == ["bug", "sqlite"]  # JSON 往返
        assert doc.summary == "一句话摘要"
        assert doc.enriched_at == "2026-08-16T00:00:00+08:00"

    def test_partial_update_keeps_other_fields(self, store) -> None:
        store.update_document_meta("d-1", title="T", tags=["a"])
        # 只更新 summary，title/tags 不受影响
        store.update_document_meta("d-1", summary="S")
        doc = store.get_document_by_id("d-1")
        assert doc is not None
        assert doc.title == "T"
        assert doc.tags == ["a"]
        assert doc.summary == "S"

    def test_missing_document_returns_false(self, store) -> None:
        assert store.update_document_meta("nope", title="x") is False

    def test_corrupted_tags_json_tolerated(self, store) -> None:
        store._conn.execute(
            "UPDATE documents SET tags = ? WHERE id = ?", ("{not json", "d-1")
        )
        doc = store.get_document_by_id("d-1")
        assert doc is not None and doc.tags is None


class TestMoveDocument:
    def test_move_updates_all_layers(self, store) -> None:
        store.ensure_collection("target-col")
        moved = store.move_document("d-1", "target-col")
        assert moved is True

        # documents 层
        doc = store.get_document_by_id("d-1")
        assert doc is not None and doc.collection == "target-col"

        # chunks_meta 层
        chunks = store.list_chunks_by_document("d-1")
        assert len(chunks) == 1 and chunks[0].collection == "target-col"

        # 向量检索：旧集合查不到，新集合查得到
        assert store.vector_search(_embed(1), top_k=10, collection="default") == []
        hits = store.vector_search(_embed(1), top_k=10, collection="target-col")
        assert len(hits) == 1

        # BM25 检索：collection 过滤随移动更新（FTS5 不可用时跳过）
        if store.fts_available:
            assert store.bm25_search("alpha", top_k=10, collection="default") == []
            bm25_hits = store.bm25_search("alpha", top_k=10, collection="target-col")
            assert len(bm25_hits) == 1

    def test_move_same_collection_noop(self, store) -> None:
        assert store.move_document("d-1", "default") is False

    def test_move_missing_document(self, store) -> None:
        assert store.move_document("nope", "other") is False

    def test_move_source_conflict_raises(self, store) -> None:
        """目标集合已有同名 source 时，UNIQUE(collection, source) 冲突报 StoreError。"""
        store.upsert_document(_doc("d-2", "col-b", "note:第一条", 0))
        with pytest.raises(StoreError):
            store.move_document("d-1", "col-b")
        # 冲突后原文档保持原集合（事务回滚）
        doc = store.get_document_by_id("d-1")
        assert doc is not None and doc.collection == "default"


class TestDeleteCleansFTS:
    def test_delete_document_removes_bm25_rows(self, store) -> None:
        """删除文档后 FTS 行同步清除（chunk_id TEXT affinity 需 CAST 匹配）。"""
        if not store.fts_available:
            pytest.skip("FTS5 不可用")
        fts_conn = store._fts_conn
        before = fts_conn.execute("SELECT COUNT(*) FROM bm25_index").fetchone()[0]
        assert before == 1

        store.delete_document("d-1")
        after = fts_conn.execute("SELECT COUNT(*) FROM bm25_index").fetchone()[0]
        assert after == 0

    def test_list_documents_filter_by_q(self, store) -> None:
        """list_documents 按 q 过滤 source / title / summary。"""
        # store fixture 已含 d-1 source="note:第一条"
        results = store.list_documents(q="第一条")
        assert len(results) == 1

        results = store.list_documents(q="不存在的")
        assert len(results) == 0

        # 不传 q 返回全部
        results = store.list_documents()
        assert len(results) >= 1

    def test_update_chunk_extra(self, store) -> None:
        """update_chunk_extra 合并更新 extra JSON 字段。"""
        # store fixture 已插入一个 chunk(id=1, extra='{}')
        ok = store.update_chunk_extra(1, {"annotation": "重要笔记"})
        assert ok is True

        # 验证 chunks_meta 中 extra 已更新
        row = store._conn.execute(
            "SELECT extra FROM chunks_meta WHERE id = 1"
        ).fetchone()
        assert row is not None
        data = json.loads(row[0])
        assert data["annotation"] == "重要笔记"

        # 不存在的 chunk_id 返回 False
        ok = store.update_chunk_extra(9999, {"annotation": "xxx"})
        assert ok is False

    def test_update_chunk_extra_merges_existing_keys(self, store) -> None:
        """update_chunk_extra 合并语义:已有 key 保留,新 key 追加。"""
        # 先写 annotation
        store.update_chunk_extra(1, {"annotation": "第一条批注"})
        # 再写另一个 key
        store.update_chunk_extra(1, {"highlight": True})
        # 验证两个 key 都在
        row = store._conn.execute(
            "SELECT extra FROM chunks_meta WHERE id = 1"
        ).fetchone()
        data = json.loads(row[0])
        assert data["annotation"] == "第一条批注"
        assert data["highlight"] is True

        # 覆盖已有 key
        store.update_chunk_extra(1, {"annotation": "更新后的批注"})
        row = store._conn.execute(
            "SELECT extra FROM chunks_meta WHERE id = 1"
        ).fetchone()
        data = json.loads(row[0])
        assert data["annotation"] == "更新后的批注"
        assert data["highlight"] is True  # 其他 key 保留

    def test_list_documents_q_escapes_wildcards(self, store) -> None:
        """q 搜索转义 LIKE 通配符 % 和 _。"""
        # store fixture 已含 d-1 source="note:第一条"
        # 正常搜索应命中
        assert len(store.list_documents(q="第一条")) == 1

        # 搜索含 % 通配符不应匹配所有文档
        results = store.list_documents(q="%")
        # % 被转义为字面量,不应匹配 "note:第一条"
        assert len(results) == 0

        # 搜索含 _ 通配符不应匹配所有文档
        results = store.list_documents(q="_")
        assert len(results) == 0
