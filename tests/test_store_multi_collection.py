"""向量存储多集合检索测试（真实临时库，不 mock）。

验证 `vector_search` / `bm25_search` 支持单集合、多集合列表、
以及 None（跨全部集合）三种集合解析。sqlite-vec 为可选的
本地扩展，不可用时跳过（与生产环境降级行为一致）。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.store.sqlite_vec import StoredDocument, VectorStore

EMBEDDING_DIM = 8


def _doc(doc_id: str, collection: str, source: str, chunks: int) -> StoredDocument:
    return StoredDocument(
        id=doc_id,
        source=source,
        collection=collection,
        format="txt",
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
    rng = np.random.default_rng(seed)
    return rng.random(EMBEDDING_DIM, dtype=np.float32)


@pytest.fixture()
def store(tmp_path) -> Any:
    db_path = tmp_path / "test.db"
    vs = VectorStore(db_path, embedding_dim=EMBEDDING_DIM)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001 — sqlite-vec 缺失则跳过
        pytest.skip(f"sqlite-vec 不可用: {e}")

    # 1 个文档撒 3 个集合，内容用英文关键词便于 trigram BM25 精确匹配
    vs.upsert_document(_doc("d-default", "default", "default.txt", 3))
    for i, content in enumerate(["alpha electron", "beta proton", "gamma neutron"]):
        vs.insert_chunks(
            document_id="d-default",
            collection="default",
            source="default.txt",
            fmt="txt",
            chunks=[_chunk(content)],
            embeddings=[_embed(100 + i)],
        )

    vs.upsert_document(_doc("d-a", "docs-a", "a.txt", 3))
    for i, content in enumerate(["alpha quark", "beta boson", "delta pion"]):
        vs.insert_chunks(
            document_id="d-a",
            collection="docs-a",
            source="a.txt",
            fmt="txt",
            chunks=[_chunk(content)],
            embeddings=[_embed(200 + i)],
        )

    vs.upsert_document(_doc("d-b", "docs-b", "b.txt", 3))
    for i, content in enumerate(["alpha meson", "gamma lepton", "epsilon photon"]):
        vs.insert_chunks(
            document_id="d-b",
            collection="docs-b",
            source="b.txt",
            fmt="txt",
            chunks=[_chunk(content)],
            embeddings=[_embed(300 + i)],
        )

    yield vs
    vs.close()


# ----------------------------------------------------------------------
# vector_search 集合过滤
# ----------------------------------------------------------------------
class TestVectorSearchCollections:
    def test_single_collection_filters(self, store) -> None:
        hits = store.vector_search(_embed(1), top_k=100, collection="docs-a")
        assert len(hits) == 3
        collections = {store.get_chunks([cid])[0].collection for cid, _ in hits}
        assert collections == {"docs-a"}

    def test_multi_collection_union(self, store) -> None:
        hits = store.vector_search(_embed(1), top_k=100, collection=["docs-a", "docs-b"])
        collections = {store.get_chunks([cid])[0].collection for cid, _ in hits}
        assert collections == {"docs-a", "docs-b"}

    def test_none_returns_all(self, store) -> None:
        hits = store.vector_search(_embed(1), top_k=100, collection=None)
        assert len(hits) == 9  # 3 集合 × 3 chunk

    def test_unknown_collection_returns_empty(self, store) -> None:
        hits = store.vector_search(_embed(1), top_k=100, collection="docs-nonexistent")
        assert hits == []

    def test_empty_list_treated_as_all(self, store) -> None:
        """空列表 / 全空白字符串等价于不过滤（返回全部）。"""
        hits = store.vector_search(_embed(1), top_k=100, collection=[])
        assert len(hits) == 9
        hits2 = store.vector_search(_embed(1), top_k=100, collection=["  "])
        assert len(hits2) == 9


# ----------------------------------------------------------------------
# bm25_search 集合过滤
# ----------------------------------------------------------------------
class TestBM25SearchCollections:
    def test_bm25_multi_collection(self, store) -> None:
        """命中词分布在不同集合时，按集合列表过滤结果。"""
        hits = store.bm25_search("alpha", top_k=100, collection=["docs-a", "docs-b"])
        assert len(hits) == 2  # docs-a 的 alpha quark + docs-b 的 alpha meson
        collections = {store.get_chunks([cid])[0].collection for cid, _ in hits}
        assert collections == {"docs-a", "docs-b"}

    def test_bm25_single_collection(self, store) -> None:
        hits = store.bm25_search("gamma", top_k=100, collection="docs-b")
        assert len(hits) == 1
        assert store.get_chunks([hits[0][0]])[0].collection == "docs-b"

    def test_bm25_none_returns_all(self, store) -> None:
        hits = store.bm25_search("alpha", top_k=100, collection=None)
        assert len(hits) == 3  # 三个集合各命中一个

    def test_bm25_unknown_collection_returns_empty(self, store) -> None:
        hits = store.bm25_search("alpha", top_k=100, collection="docs-nonexistent")
        assert hits == []
