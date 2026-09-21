"""重排（Reranker）集成测试 — 锁定 rerank 精排与优雅降级行为。

背景：召回阶段 BM25+向量+RRF 只按排名位置融合，不衡量 query 与文档的
真实语义相关度。加入 cross-encoder 重排后：
- 启用重排：候选按重排分重新排序，hits 携带 rerank_score；
- 重排器抛错：降级为原始 RRF 排序，检索不崩溃，stats.reranked=False。
"""

from __future__ import annotations

from typing import Any

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.reranker.base import Reranker, RerankerError
from doc2mind.core.retriever.search import Retriever
from doc2mind.core.store.sqlite_vec import StoredDocument, VectorStore

EMBEDDING_DIM = 8


class _FlatEmbedder:
    """恒定向量嵌入器：向量路对所有内容等距，排序由 BM25 决定（便于预测）。"""

    dimension = EMBEDDING_DIM

    def embed_query(self, text: str) -> list[float]:
        return [1.0] * EMBEDDING_DIM


class _FakeReranker(Reranker):
    """确定性重排器：命中"公司"关键词的文档给低分、否则给高分（用于反转 RRF 序）。"""

    @property
    def model_name(self) -> str:
        return "fake-reranker"

    def rerank(self, query: str, documents: list[str], batch_size: int = 32) -> list[float]:
        return [1.0 if "公司" not in d else 0.1 for d in documents]


class _BrokenReranker(Reranker):
    """重排器不可用：rerank 直接抛 RerankerError（验证降级）。"""

    @property
    def model_name(self) -> str:
        return "broken-reranker"

    def rerank(self, query: str, documents: list[str], batch_size: int = 32) -> list[float]:
        raise RerankerError("重排模型加载失败（测试注入）")


def _add_doc(
    store: VectorStore, doc_id: str, collection: str, source: str, content: str,
) -> None:
    store.upsert_document(StoredDocument(
        id=doc_id, source=source, collection=collection, format="md",
        file_hash=f"hash-{doc_id}", size_bytes=100, page_count=None,
        chunk_count=1,
        created_at="2026-01-01T00:00:00+08:00",
        updated_at="2026-01-01T00:00:00+08:00",
    ))
    store.insert_chunks(
        document_id=doc_id, collection=collection, source=source, fmt="md",
        chunks=[Chunk(content=content, tokens=len(content), metadata={"chunk_index": 0})],
        embeddings=[[1.0] * EMBEDDING_DIM],
    )


@pytest.fixture()
def store(tmp_path) -> Any:
    vs = VectorStore(tmp_path / "rerank.db", embedding_dim=EMBEDDING_DIM)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"sqlite-vec 不可用: {e}")
    yield vs
    vs.close()


def _seed(store: VectorStore) -> None:
    # 查询 "苹果 公司 股价"：BM25 下 b.md（含"公司"）强于 a.md（仅"苹果"）
    _add_doc(store, "d-a", "t", "a.md", "苹果 水果 营养 维生素")
    _add_doc(store, "d-b", "t", "b.md", "苹果 公司 手机 发布")
    _add_doc(store, "d-c", "t", "c.md", "香蕉 蔬菜 沙拉")  # 填充，避免 IDF 钳零


class TestRerankOrdering:
    def test_rerank_reorders_candidates(self, store) -> None:
        """启用重排后，命中序应按重排分重排，且 hits 携带 rerank_score。"""
        _seed(store)
        retriever = Retriever(
            store=store, embedder=_FlatEmbedder(), reranker=_FakeReranker(),
        )
        hits, stats = retriever.search("苹果 公司 股价", collection="t", top_k=5)

        assert stats.reranked is True
        assert stats.rerank_model == "fake-reranker"
        # 重排器偏好不含"公司"的 a.md → 应排到最前
        assert hits[0].chunk.source == "a.md"
        # 每个命中都应有重排分
        assert all(h.rerank_score is not None for h in hits)
        # b.md 的重排分应低于 a.md
        a = next(h for h in hits if h.chunk.source == "a.md")
        b = next(h for h in hits if h.chunk.source == "b.md")
        assert a.rerank_score > b.rerank_score
        # 重排分经 sigmoid 归一化为 0-1 相关度概率，不再出现 logits 的负数或 >1
        assert all(0.0 <= h.rerank_score <= 1.0 for h in hits)


class TestRerankDegradation:
    def test_rerank_error_falls_back_to_rrf(self, store) -> None:
        """重排器抛错必须降级：保留 RRF 序、不崩溃、reranked=False。"""
        _seed(store)
        # 基线：无重排器的原始 RRF 顺序
        baseline, _ = Retriever(store=store, embedder=_FlatEmbedder()).search(
            "苹果 公司 股价", collection="t", top_k=5
        )
        baseline_order = [h.chunk.source for h in baseline]

        retriever = Retriever(
            store=store, embedder=_FlatEmbedder(), reranker=_BrokenReranker(),
        )
        # 不应抛异常
        hits, stats = retriever.search("苹果 公司 股价", collection="t", top_k=5)

        assert stats.reranked is False
        assert stats.degraded is True
        assert stats.degraded_reason and "重排" in stats.degraded_reason
        # 降级后顺序必须与原始 RRF 顺序一致（未被重排改变）
        assert [h.chunk.source for h in hits] == baseline_order
        # 降级时 rerank_score 为 None
        assert all(h.rerank_score is None for h in hits)


class TestNoReranker:
    def test_default_no_rerank_score(self, store) -> None:
        """未传重排器时行为与原来一致：reranked=False，rerank_score=None。"""
        _seed(store)
        retriever = Retriever(store=store, embedder=_FlatEmbedder())
        hits, stats = retriever.search("苹果 公司 股价", collection="t", top_k=5)

        assert stats.reranked is False
        assert all(h.rerank_score is None for h in hits)
