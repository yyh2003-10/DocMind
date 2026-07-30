"""检索器 — BM25 + 向量余弦，RRF 融合。

流程：
    query → embed_query
         → vector_search (余弦距离)
         → bm25_search   (FTS5)
         → RRF 融合排序
         → 取 top_k
         → 加载 chunks_meta 渲染 SearchHit

RRF (Reciprocal Rank Fusion) 公式：
    score(d) = Σ_{r ∈ rankings} 1 / (k + rank_r(d))
默认 k = 60（ Cormack 等 2009）。

距离 → score 转换：
    vec0 cosine distance ∈ [0, 2]，distance=0 表示完全相同
    score = 1 / (1 + distance)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.embedder.base import Embedder
from doc2mind.core.store.sqlite_vec import StoredChunk, VectorStore


class RetrievalError(Exception):
    """检索异常。"""


@dataclass(frozen=True)
class SearchHit:
    """检索命中项。"""

    chunk: "StoredChunkMeta"
    score: float
    match_type: str  # vector | bm25 | hybrid
    vector_score: float
    bm25_score: float
    rank: int


@dataclass(frozen=True)
class StoredChunkMeta:
    """检索结果中的 chunk 元数据简化版。"""

    id: int
    content: str
    source: str
    format: str
    doc_type: str | None
    page: int | None
    heading: str | None
    tokens: int
    chunk_index: int
    collection: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchStats:
    """检索统计。"""

    query: str
    total_hits: int
    elapsed_ms: int
    vector_candidates: int
    bm25_candidates: int


class Retriever:
    """混合检索器。

    Args:
        store: 向量存储
        embedder: 嵌入引擎
        rrf_k: RRF 常数，默认 60
    """

    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        rrf_k: int = 60,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.rrf_k = max(1, int(rrf_k))

    # --- 主入口 ---
    def search(
        self,
        query: str,
        collection: str | None = "default",
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> tuple[list[SearchHit], SearchStats]:
        """混合检索。

        Args:
            query: 查询文本
            collection: 集合名，None 表示跨所有集合
            top_k: 返回结果数
            min_score: 过滤低分结果

        Returns:
            (hits, stats)，hits 按 score 降序
        """
        import time

        t0 = time.perf_counter()
        try:
            # 1. 嵌入查询
            query_vec = self.embedder.embed_query(query)

            # 2. 向量检索（取 top_k * 3 候选）
            vec_candidates_n = top_k * 3
            vec_hits = self.store.vector_search(
                query_vec, top_k=vec_candidates_n, collection=collection
            )
            # 距离 → score
            vec_scored = [
                (cid, _distance_to_score(dist), dist) for cid, dist in vec_hits
            ]

            # 3. BM25 检索
            bm25_hits = self.store.bm25_search(
                query, top_k=top_k * 3, collection=collection
            )
            # BM25 score 归一化到 [0, 1]：score / (1 + score)
            bm25_scored = [
                (cid, _bm25_normalize(s), s) for cid, s in bm25_hits
            ]

            # 4. RRF 融合
            fused = _rrf_fuse(
                vec_ranking=[(cid, sc) for cid, sc, _ in vec_scored],
                bm25_ranking=[(cid, sc) for cid, sc, _ in bm25_scored],
                k=self.rrf_k,
            )

            # 5. 取 top_k，过滤 min_score
            fused.sort(key=lambda x: x[1], reverse=True)
            top_hits: list[tuple[int, float, float, float]] = []
            for cid, rrf_score, v_score, b_score in fused:
                if rrf_score < min_score:
                    continue
                top_hits.append((cid, rrf_score, v_score, b_score))
                if len(top_hits) >= top_k:
                    break

            # 6. 加载元数据
            chunk_ids = [h[0] for h in top_hits]
            stored = self.store.get_chunks(chunk_ids)
            id_to_stored = {s.id: s for s in stored}

            # 7. 渲染 SearchHit
            hits: list[SearchHit] = []
            for rank, (cid, rrf_score, v_score, b_score) in enumerate(top_hits):
                s = id_to_stored.get(cid)
                if s is None:
                    continue
                meta = StoredChunkMeta(
                    id=s.id,
                    content=s.content,
                    source=s.source,
                    format=s.format,
                    doc_type=s.doc_type,
                    page=s.page,
                    heading=s.heading,
                    tokens=s.tokens,
                    chunk_index=s.chunk_index,
                    collection=s.collection,
                    extra=s.extra_metadata,
                )
                match_type = _match_type(v_score, b_score)
                hits.append(
                    SearchHit(
                        chunk=meta,
                        score=rrf_score,
                        match_type=match_type,
                        vector_score=v_score,
                        bm25_score=b_score,
                        rank=rank,
                    )
                )

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            stats = SearchStats(
                query=query,
                total_hits=len(hits),
                elapsed_ms=elapsed_ms,
                vector_candidates=len(vec_scored),
                bm25_candidates=len(bm25_scored),
            )
            return hits, stats

        except RetrievalError:
            raise
        except Exception as e:  # noqa: BLE001
            raise RetrievalError(f"检索失败: {e}") from e


# --- RRF 融合 ---
def _rrf_fuse(
    vec_ranking: list[tuple[int, float]],
    bm25_ranking: list[tuple[int, float]],
    k: int,
) -> list[tuple[int, float, float, float]]:
    """RRF 融合两路排序。

    Args:
        vec_ranking: [(chunk_id, score), ...] 按 score 降序
        bm25_ranking: 同上
        k: RRF 常数

    Returns:
        [(chunk_id, rrf_score, vec_score, bm25_score), ...]
    """
    # 排名（1-based）
    vec_sorted = sorted(vec_ranking, key=lambda x: x[1], reverse=True)
    bm25_sorted = sorted(bm25_ranking, key=lambda x: x[1], reverse=True)

    vec_rank = {cid: rank + 1 for rank, (cid, _) in enumerate(vec_sorted)}
    bm25_rank = {cid: rank + 1 for rank, (cid, _) in enumerate(bm25_sorted)}

    vec_score_map = {cid: sc for cid, sc in vec_sorted}
    bm25_score_map = {cid: sc for cid, sc in bm25_sorted}

    all_ids = set(vec_rank) | set(bm25_rank)
    result: list[tuple[int, float, float, float]] = []
    for cid in all_ids:
        v_rank = vec_rank.get(cid)
        b_rank = bm25_rank.get(cid)
        rrf = 0.0
        if v_rank is not None:
            rrf += 1.0 / (k + v_rank)
        if b_rank is not None:
            rrf += 1.0 / (k + b_rank)
        v_sc = vec_score_map.get(cid, 0.0)
        b_sc = bm25_score_map.get(cid, 0.0)
        result.append((cid, rrf, v_sc, b_sc))
    return result


# --- 分数转换 ---
def _distance_to_score(distance: float) -> float:
    """vec0 cosine distance → [0, 1] 相似度分数。

    distance ∈ [0, 2]，0 = 完全相同，2 = 完全相反。
    """
    # 用 1/(1+d) 平滑映射，避免负值
    return 1.0 / (1.0 + max(0.0, distance))


def _bm25_normalize(score: float) -> float:
    """BM25 原始 score → [0, 1] 归一化。"""
    if score <= 0:
        return 0.0
    return score / (1.0 + score)


def _match_type(v_score: float, b_score: float) -> str:
    """判断命中类型。"""
    if v_score > 0 and b_score > 0:
        return "hybrid"
    if v_score > 0:
        return "vector"
    if b_score > 0:
        return "bm25"
    return "unknown"
