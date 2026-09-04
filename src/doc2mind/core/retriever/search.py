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
    score = similarity = 1 - distance（忠实余弦相似度）
    （d≈0.67 真实余弦 0.33 → 0.33；d=1 正交 → 0）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from doc2mind.core.embedder.base import Embedder, EmbedderError
from doc2mind.core.reranker.base import Reranker, RerankerError
from doc2mind.core.store.sqlite_vec import StoreError, VectorStore


class RetrievalError(Exception):
    """检索异常。"""


@dataclass(frozen=True)
class SearchHit:
    """检索命中项。"""

    chunk: StoredChunkMeta
    score: float
    match_type: str  # vector | bm25 | hybrid
    vector_score: float
    bm25_score: float
    rank: int
    # 稀疏向量余弦（D2 三路召回）：词法稀疏路 cosine，与 vector_score 同量纲。
    # 未启用稀疏路或未命中时为 0.0。
    sparse_score: float = 0.0
    # 重排相关性分：经 sigmoid 归一化的相关度概率（0-1，越大越相关）。
    # 重排器原始输出为 cross-encoder logits（可负、可 >1），Retriever 在此统一
    # 归一化，避免下游展示出「相关度 5.32」或负数、或与 0-1 阈值混用。
    # None = 未启用重排。仅用于精排与展示，比融合分更能反映真实相关度。
    rerank_score: float | None = None


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
    # 稀疏向量路（D2）候选数与是否启用。
    sparse_candidates: int = 0
    sparse_enabled: bool = False
    degraded: bool = False  # True = 嵌入服务不可用，已降级为纯 BM25
    # 降级原因（面向用户的中文提示；degraded=True 时非空）
    degraded_reason: str | None = None
    # 其他提示（如 min_score 超出 RRF 分数范围被忽略）
    message: str | None = None
    # 是否启用了重排（cross-encoder 精排）；False = 用原始 RRF 排序
    reranked: bool = False
    # 实际使用的重排模型名（reranked=True 时非空）
    rerank_model: str | None = None


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
        reranker: "Reranker | None" = None,
        rerank_recall: int = 20,
        query_instruction: str = "",
        semantic_floor: float = 0.0,
        rrf_weights: tuple[float, float, float] | tuple[float, float] = (1.0, 1.0),
        fusion_mode: str = "rrf",
        rerank_calibration_temperature: float = 1.0,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.rrf_k = max(1, int(rrf_k))
        # 重排器（可选）：对 RRF 召回候选做 query-doc 相关性精排。
        # None = 不重排，保留原始 RRF 排序；推理失败由 search() 内部降级处理。
        self.reranker = reranker
        # 送入重排器的候选数上限（从 RRF 结果中截取最靠前若干条）。
        # 这是"下限"：实际送入候选随 top_k 动态放大（B3：top_k 大时 20 会漏召回），
        # 取 max(配置值, top_k*3)，再与可用候选数取小。不宜过大以免拖慢推理。
        self.rerank_recall = max(1, int(rerank_recall))
        # 非对称检索查询指令前缀：拼在查询前再 embed_query（不动文档嵌入）。
        # 对 bge 系列可显著提升检索；空 = 不启用。
        self.query_instruction = query_instruction or ""
        # 语义下限：命中的相关度代理低于该值则丢弃；0 表示不启用。
        self.semantic_floor = max(0.0, float(semantic_floor))
        # RRF 融合权重（vec, bm25, sparse），默认中性 1:1，稀疏路默认权重 0
        # （向后兼容：只给 2 路权重时稀疏路不参与）。
        w = [max(0.0, float(x)) for x in rrf_weights][:3]
        while len(w) < 3:
            w.append(0.0)
        self.rrf_weights = (w[0], w[1], w[2])
        # 融合模式："rrf"（加权倒数排名，默认）| "score"（加权分数融合）。
        self.fusion_mode = fusion_mode if fusion_mode in ("rrf", "score") else "rrf"
        # 重排分校准温度：sigmoid(logit/T) 后才作为 0-1 相关度展示。
        # 1.0 = 原 plain sigmoid（向后兼容）；0<T<1 更陡、>1 更平缓。
        self.rerank_temperature = (
            1.0 if float(rerank_calibration_temperature) <= 0.0
            else float(rerank_calibration_temperature)
        )

    # --- 主入口 ---
    def search(
        self,
        query: str,
        collection: str | list[str] | None = "default",
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> tuple[list[SearchHit], SearchStats]:
        """混合检索。

        Args:
            query: 查询文本
            collection: 集合名或集合名列表（多选知识库），None 表示跨所有集合
            top_k: 返回结果数
            min_score: 过滤低分结果（RRF 融合分量纲，仅专家调参用）

        Returns:
            (hits, stats)，hits 按 score 降序
        """
        import time

        t0 = time.perf_counter()
        degraded = False
        degraded_reason: str | None = None
        message: str | None = None
        try:
            # 1. 嵌入查询
            # 降级①：嵌入服务不可用（API key 缺失/网络失败/模型加载失败）时
            # 跳过向量路，仅用 BM25 全文检索，避免整个搜索 500。
            # 由调用方通过 stats.degraded / degraded_reason 提示用户降级原因。
            # 非对称检索：先拼查询指令前缀（bge 系），再 embed_query。
            embed_query_text = (
                f"{self.query_instruction}{query}" if self.query_instruction else query
            )
            try:
                query_vec = self.embedder.embed_query(embed_query_text)
            except EmbedderError as e:
                degraded = True
                degraded_reason = f"嵌入服务不可用（{e}），本次为纯 BM25 检索，效果可能下降"
                query_vec = None

            # 2. 向量检索（取 top_k * 3 候选）
            # 降级②：向量索引不可用（典型：运行时换嵌入模型后维度不匹配且
            # 未重建索引）时同样跳过向量路，仅用 BM25。
            vec_scored: list[tuple[int, float, float]] = []
            if query_vec is not None:
                vec_candidates_n = top_k * 3
                try:
                    vec_hits = self.store.vector_search(
                        query_vec, top_k=vec_candidates_n, collection=collection
                    )
                    # 距离 → score
                    vec_scored = [
                        (cid, _distance_to_score(dist), dist) for cid, dist in vec_hits
                    ]
                except StoreError as e:
                    degraded = True
                    low = str(e).lower()
                    if "dim" in low or "dimension" in low or "维" in str(e):
                        degraded_reason = (
                            "向量维度与索引不一致（换嵌入模型后未重建索引），"
                            "本次为纯 BM25 检索；请在设置页执行「重建索引」"
                        )
                    else:
                        degraded_reason = f"向量检索不可用（{e}），本次为纯 BM25 检索"
                    vec_scored = []

            # 3. BM25 检索
            bm25_hits = self.store.bm25_search(
                query, top_k=top_k * 3, collection=collection
            )
            # BM25 score 归一化到 [0, 1]：score / (1 + score)
            bm25_scored = [
                (cid, _bm25_normalize(s), s) for cid, s in bm25_hits
            ]

            # 3.5 稀疏向量检索（D2，第三条召回路）：仅当存储启用稀疏索引、且
            # 融合权重里稀疏路 >0 时参与，否则保持两路（向后兼容）。
            sparse_scored: list[tuple[int, float]] = []
            sparse_enabled = self.store.sparse_available and self.rrf_weights[2] > 0.0
            if sparse_enabled:
                sparse_scored = self.store.sparse_search(
                    query, top_k=top_k * 3, collection=collection
                )  # [(chunk_id, cosine∈[0,1])]

            # 4. 融合（加权 RRF 或加权分数，由 fusion_mode 决定）
            fused = _fuse(
                mode=self.fusion_mode,
                vec_ranking=[(cid, sc) for cid, sc, _ in vec_scored],
                bm25_ranking=[(cid, sc) for cid, sc, _ in bm25_scored],
                sparse_ranking=sparse_scored or None,
                k=self.rrf_k,
                weights=self.rrf_weights,
            )
            sparse_score_map = dict(sparse_scored)

            # 4.5 重排（可选）：cross-encoder 对 RRF 召回候选逐对打分，纠正排序偏差
            # 召回阶段（BM25+向量+RRF）只按排名位置融合，不衡量 query 与文档的
            # 真实语义相关度；重排能显著提升精度。重排不可用则优雅降级为原始 RRF 排序。
            reranked = False
            rerank_scores: dict[int, float] = {}
            rerank_model_name: str | None = (
                self.reranker.model_name if self.reranker is not None else None
            )
            if self.reranker is not None and fused:
                # B3：重排候选数随 top_k 动态放大（max(配置值, top_k*3)），
                # 避免 top_k 较大时召回不足；再与可用候选数取小。
                recall = min(
                    len(fused),
                    max(self.rerank_recall, int(top_k) * 3),
                )
                cand = fused[:recall]  # 取前 recall 个候选（顺序无关，重排会重新评估）
                cand_ids = [c[0] for c in cand]
                try:
                    cand_stored = self.store.get_chunks(cand_ids)
                    id_to_content = {st.id: st.content for st in cand_stored}
                    docs = [id_to_content.get(cid, "") for cid in cand_ids]
                    raw_scores = self.reranker.rerank(query, docs)
                    # 重排器返回原始 logits（可负、可 >1）；先做温度缩放校准，再 sigmoid
                    # 归一化到 0-1 相关度概率再下游（排序/展示/过滤），避免展示出
                    # 「相关度 5.32」或负数。sigmoid/T 单调递增，不影响重排排序结果。
                    rerank_scores = {
                        cid: _sigmoid(s, temperature=self.rerank_temperature)
                        for cid, s in zip(cand_ids, raw_scores)
                    }
                    reranked = True
                except RerankerError as e:
                    # 重排不可用（模型未装/下载失败/推理异常）→ 降级，不阻断检索
                    degraded = True
                    degraded_reason = (
                        f"重排模型不可用（{e}），本次为原始 RRF 排序"
                    )
                    rerank_scores = {}

            # 5. 取 top_k，过滤 min_score
            # min_score 过滤的是 RRF 融合分（量纲 ≈ 2/(k+1)，k=60 时 ≈ 0.033），
            # 用户直觉填 0.5 会过滤掉全部结果 —— 越界时忽略并提示，而不是静默清空。
            rrf_ceiling = 2.0 / (self.rrf_k + 1)
            if min_score > rrf_ceiling:
                message = (
                    f"min_score={min_score:g} 超出 RRF 融合分数范围"
                    f"（0 ~ {rrf_ceiling:.4f}），已忽略该过滤条件；"
                    "RRF 分数普遍很小，请参考结果里的 score 值设置"
                )
                min_score = 0.0
            # 重排后用 rerank 分数重排（候选优先，非候选置末尾）；未重排保持原 RRF 顺序
            if reranked:
                fused.sort(
                    key=lambda x: rerank_scores.get(x[0], float("-inf")),
                    reverse=True,
                )
            else:
                fused.sort(key=lambda x: x[1], reverse=True)
            top_hits: list[tuple[int, float, float, float]] = []
            for cid, rrf_score, v_score, b_score in fused:
                if rrf_score < min_score:
                    continue
                top_hits.append((cid, rrf_score, v_score, b_score))
                if len(top_hits) >= top_k:
                    break

            # 5.5 语义下限：丢弃相关度代理低于阈值的弱命中（top_k 是上限，不硬填）。
            # 代理：重排启用用 reRank 分，否则用 max(vector, bm25)。
            if self.semantic_floor > 0.0 and top_hits:
                before = len(top_hits)
                kept: list[tuple[int, float, float, float]] = []
                for cid, rrf_score, v_score, b_score in top_hits:
                    proxy = (
                        rerank_scores.get(cid)
                        if reranked
                        else max(v_score, b_score)
                    )
                    if proxy is not None and proxy >= self.semantic_floor:
                        kept.append((cid, rrf_score, v_score, b_score))
                    elif reranked and rerank_scores.get(cid) is None:
                        # 重排召回非候选（未打分）：保留，避免误删跨阈值之外的候选
                        kept.append((cid, rrf_score, v_score, b_score))
                top_hits = kept
                if len(kept) < before:
                    message = (
                        f"已按相关度下限 {self.semantic_floor:g} 过滤 "
                        f"{before - len(kept)} 条低相关结果"
                    )

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
                        sparse_score=sparse_score_map.get(cid, 0.0),
                        rerank_score=rerank_scores.get(cid) if reranked else None,
                    )
                )

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            stats = SearchStats(
                query=query,
                total_hits=len(hits),
                elapsed_ms=elapsed_ms,
                vector_candidates=len(vec_scored),
                bm25_candidates=len(bm25_scored),
                sparse_candidates=len(sparse_scored),
                sparse_enabled=sparse_enabled,
                degraded=degraded,
                degraded_reason=degraded_reason,
                message=message,
                reranked=reranked,
                rerank_model=rerank_model_name if reranked else None,
            )
            return hits, stats

        except RetrievalError:
            raise
        except Exception as e:  # noqa: BLE001
            raise RetrievalError(f"检索失败: {e}") from e


# --- 融合 ---
def _unpack3(weights: tuple[float, ...]) -> tuple[float, float, float]:
    """把 2/3 元权重统一为 3 元（缺省补 0，第三位=稀疏路）。"""
    w = [max(0.0, float(x)) for x in weights][:3]
    while len(w) < 3:
        w.append(0.0)
    return (w[0], w[1], w[2])


def _fuse(
    *,
    mode: str,
    vec_ranking: list[tuple[int, float]],
    bm25_ranking: list[tuple[int, float]],
    k: int,
    weights: tuple[float, ...] = (1.0, 1.0),
    sparse_ranking: list[tuple[int, float]] | None = None,
) -> list[tuple[int, float, float, float]]:
    """融合多路排序（向量 + BM25 [+ 稀疏向量]）为一路，返回 [(cid, fused, vec, bm25), ...]。

    Args:
        mode: "rrf" 加权倒数排名融合（稳健，分数尺度不敏感）；
              "score" 加权分数融合（善用分数信息，要求两路分数已校准）。
        vec_ranking / bm25_ranking: [(chunk_id, score), ...]
        sparse_ranking: 可选第三路（D2 稀疏向量）：[(chunk_id, cosine), ...]
        k: RRF 常数（仅 mode="rrf" 使用）
        weights: (vec 权重, bm25 权重[, 稀疏权重])；默认 1:1 中性（3 路时权重默认为
            第三路 0，不启用稀疏路，向后兼容）。
    """
    if mode == "score":
        return _score_fuse(vec_ranking, bm25_ranking, weights, sparse_ranking)
    return _rrf_fuse(vec_ranking, bm25_ranking, k, weights, sparse_ranking)


def _rrf_fuse(
    vec_ranking: list[tuple[int, float]],
    bm25_ranking: list[tuple[int, float]],
    k: int,
    weights: tuple[float, ...] = (1.0, 1.0),
    sparse_ranking: list[tuple[int, float]] | None = None,
) -> list[tuple[int, float, float, float]]:
    """加权 RRF 融合多路排序。

    Args:
        vec_ranking / bm25_ranking / sparse_ranking: 各路 [(chunk_id, score), ...]
            按 score 降序；sparse_ranking 可为空（两路融合）。
        k: RRF 常数
        weights: (vec 权重, bm25 权重[, 稀疏权重])

    Returns:
        [(chunk_id, rrf_score, vec_score, bm25_score), ...]
    """
    # 排名（1-based）
    vec_sorted = sorted(vec_ranking, key=lambda x: x[1], reverse=True)
    bm25_sorted = sorted(bm25_ranking, key=lambda x: x[1], reverse=True)
    sparse_sorted = (
        sorted(sparse_ranking, key=lambda x: x[1], reverse=True)
        if sparse_ranking else []
    )

    vec_rank = {cid: rank + 1 for rank, (cid, _) in enumerate(vec_sorted)}
    bm25_rank = {cid: rank + 1 for rank, (cid, _) in enumerate(bm25_sorted)}
    sparse_rank = {cid: rank + 1 for rank, (cid, _) in enumerate(sparse_sorted)}

    vec_score_map = dict(vec_sorted)
    bm25_score_map = dict(bm25_sorted)

    w_v, w_b, w_s = _unpack3(weights)
    all_ids = set(vec_rank) | set(bm25_rank) | set(sparse_rank)
    result: list[tuple[int, float, float, float]] = []
    for cid in all_ids:
        v_rank = vec_rank.get(cid)
        b_rank = bm25_rank.get(cid)
        s_rank = sparse_rank.get(cid)
        rrf = 0.0
        if v_rank is not None:
            rrf += w_v / (k + v_rank)
        if b_rank is not None:
            rrf += w_b / (k + b_rank)
        if s_rank is not None:
            rrf += w_s / (k + s_rank)
        v_sc = vec_score_map.get(cid, 0.0)
        b_sc = bm25_score_map.get(cid, 0.0)
        result.append((cid, rrf, v_sc, b_sc))
    return result


def _score_fuse(
    vec_ranking: list[tuple[int, float]],
    bm25_ranking: list[tuple[int, float]],
    weights: tuple[float, ...] = (1.0, 1.0),
    sparse_ranking: list[tuple[int, float]] | None = None,
) -> list[tuple[int, float, float, float]]:
    """加权分数融合多路排序。

    向量分（忠实余弦 0-1）、BM25 归一化分（0-1）与稀疏向量余弦（0-1）可比时，
    直接用加权求和作为融合分：fused = w_v*vec + w_b*bm25 + w_s*sparse，
    善用分数强度而非仅排名位置。仅部分路命中的文档按已命中的路加权计。

    Returns:
        [(chunk_id, fused_score, vec_score, bm25_score), ...]
    """
    w_v, w_b, w_s = _unpack3(weights)
    v_map = dict(vec_ranking)
    b_map = dict(bm25_ranking)
    s_map = dict(sparse_ranking or {})
    result: list[tuple[int, float, float, float]] = []
    for cid in set(v_map) | set(b_map) | set(s_map):
        v = v_map.get(cid, 0.0)
        b = b_map.get(cid, 0.0)
        s = s_map.get(cid, 0.0)
        result.append((cid, w_v * v + w_b * b + w_s * s, v, b))
    return result


# --- 分数转换 ---
def _distance_to_score(distance: float) -> float:
    """vec0 cosine distance → 忠实余弦相似度分数。

    distance ∈ [0, 2]，0 = 完全相同，2 = 完全相反。
    相似度 = 1 - distance，并钳制到 [0, 1]：
        d=0   → 1.0（完全一致）
        d=0.67 → 0.33（真实余弦 0.33）
        d=1   → 0.0（正交，语义零重叠）
        d=2   → 0.0（完全相反）
    负数（异常输入）钳制为 1.0。
    """
    return min(1.0, max(0.0, 1.0 - distance))


def _sigmoid(x: float, temperature: float = 1.0) -> float:
    """重排器 logits → [0, 1] 相关度，数值安全（防 exp 溢出）。

    Args:
        x: cross-encoder 原始 logits（可为负、可 >1）
        temperature: 校准温度，先做 x/temperature 缩放再 sigmoid——0<T<1 让结果
            更陡（更自信地分辨强弱），>1 更平缓（更保守）。默认 1.0 为原 plain
            sigmoid，向后兼容。单调递增，不影响排序。
    """
    z = x / temperature if temperature > 0 else x
    if z >= 30.0:
        return 1.0
    if z <= -30.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


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
