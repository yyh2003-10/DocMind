"""本库检索评估核心 — 在真实 VectorStore 上抽样测自检索基线。

与 `tools/eval_retrieval.py`（合成语料、CI 门槛）分工：
本模块回答「我的库现在检索准不准」，供 HTTP `/v1/eval/library`、CLI
`tools/eval_library.py` 与测试复用。确定性依赖 Random(seed)，可回归。
"""

from __future__ import annotations

import random
import re
from typing import Any

from doc2mind.core.config import (
    RECOMMENDED_RETRIEVAL_PRESET,
    Settings,
    get_settings,
    parse_rrf_weights,
)
from doc2mind.core.retriever.search import Retriever

# 自检索门槛：无需人工标注，门槛可略高于弱标注集。
MIN_SELF_RECALL_AT_1 = 0.50
MIN_SELF_RECALL_AT_3 = 0.75
MIN_MRR = 0.55


def pick_query_from_content(content: str, max_len: int = 80) -> str:
    """从 chunk 正文构造查询片段：优先完整短句。"""
    text = (content or "").strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = text.replace("|", " ").replace("```", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    m = re.search(r"[。！？.!?；;]", text)
    if m and m.start() >= 8:
        q = text[: m.start() + 1]
    else:
        q = text
    q = q[:max_len] if len(q) < 12 or len(q) > max_len else q
    if len(q) > max_len:
        q = q[:max_len]
    return q.strip(" ，,、：:；;")


def metrics_from_ranks(ranks: list[int | None]) -> dict[str, float]:
    """从每条查询的「相关块首次排名」计算指标。rank 从 1 起，None=未命中。"""
    n = len(ranks) or 1
    hits1 = sum(1 for r in ranks if r is not None and r <= 1)
    hits3 = sum(1 for r in ranks if r is not None and r <= 3)
    hits5 = sum(1 for r in ranks if r is not None and r <= 5)
    mrr = 0.0
    for r in ranks:
        if r is not None:
            mrr += 1.0 / r
    return {
        "n_queries": len(ranks),
        "SelfRecall@1": round(hits1 / n, 4),
        "SelfRecall@3": round(hits3 / n, 4),
        "SelfRecall@5": round(hits5 / n, 4),
        "MRR": round(mrr / n, 4),
    }


def sample_chunk_rows(
    store: Any,
    collection: str | None,
    limit: int,
    seed: int,
) -> list[tuple[Any, str, Any, str, str | None]]:
    """从 chunks_meta 确定性抽样 (id, content, document_id, source, heading)。"""
    sql = (
        "SELECT id, content, document_id, source, heading FROM chunks_meta "
        "WHERE length(content) >= 20"
    )
    params: list[Any] = []
    if collection:
        sql += " AND collection = ?"
        params.append(collection)
    sql += " ORDER BY id LIMIT 5000"
    with store._lock:  # noqa: SLF001
        store._require_open()  # noqa: SLF001
        rows = store._conn.execute(sql, params).fetchall()  # noqa: SLF001
    if not rows:
        return []
    rng = random.Random(seed)
    if len(rows) > limit:
        rows = rng.sample(rows, limit)
    return [(r[0], r[1] or "", r[2], r[3] or "", r[4]) for r in rows]


def _empty_report(
    collection: str | None,
    health: dict[str, Any],
    reason: str,
    message: str,
) -> dict[str, Any]:
    return {
        "engine": None,
        "collection": collection,
        "health": health,
        "metrics": {"n_queries": 0},
        "per_query": [],
        "gate": {
            "min_self_recall@1": MIN_SELF_RECALL_AT_1,
            "min_self_recall@3": MIN_SELF_RECALL_AT_3,
            "min_mrr": MIN_MRR,
        },
        "gate_passed": False,
        "recommendations": [message],
        "degraded_reason": reason,
    }


def evaluate_library(
    store: Any,
    embedder: Any,
    collection: str | None = None,
    sample: int = 30,
    seed: int = 42,
    top_k: int = 5,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """在真实库上抽样评估自检索基线。

    Returns:
        {engine, collection, health, metrics, per_query, gate, gate_passed,
         recommendations, degraded_reason?}
    """
    s = settings if settings is not None else get_settings()
    docs = store.list_documents(collection=collection, limit=10000) or []
    real_docs = [
        d for d in docs
        if getattr(d, "source", "") != "__collection_placeholder__"
    ]

    store_dim = getattr(store, "embedding_dim", None)
    cfg_dim = getattr(s, "embed_dim", None)
    health: dict[str, Any] = {
        "total_documents": len(real_docs),
        "total_chunks": sum(
            int(getattr(d, "chunk_count", 0) or 0) for d in real_docs
        ),
        "embed_model": getattr(s, "embed_model", ""),
        "store_dim": store_dim,
        "cfg_dim": cfg_dim,
        "dim_mismatch": bool(
            store_dim and cfg_dim and int(store_dim) != int(cfg_dim)
        ),
        "empty_documents": sum(
            1 for d in real_docs if int(getattr(d, "chunk_count", 0) or 0) == 0
        ),
    }

    if not real_docs:
        return _empty_report(
            collection, health, "empty_library",
            "知识库为空或当前集合无文档，请先 ingest 或指定其他 collection",
        )

    chunk_rows = sample_chunk_rows(store, collection, max(1, int(sample)), seed)
    if not chunk_rows:
        return _empty_report(
            collection, health, "no_chunks",
            "未能从库中抽样到任何分块",
        )

    degraded = False
    degraded_reason = None
    if embedder is None:
        degraded = True
        degraded_reason = "未提供 embedder，本次为纯 BM25 自检索（向量路关闭）"

    retriever = Retriever(
        store,
        embedder,
        reranker=None,
        query_instruction=str(s.query_instruction or ""),
        semantic_floor=float(s.semantic_floor or 0.0),
        rrf_weights=parse_rrf_weights(getattr(s, "rrf_weights", "1,1")),
        fusion_mode=str(getattr(s, "fusion_mode", "rrf") or "rrf"),
    )

    per_query: list[dict[str, Any]] = []
    ranks: list[int | None] = []
    same_doc_hits = 0
    for chunk_id, content, doc_id, source, heading in chunk_rows:
        query = pick_query_from_content(content)
        if not query:
            continue
        try:
            hits, stats = retriever.search(
                query,
                collection=collection,
                top_k=int(top_k),
                min_score=0.0,
            )
        except Exception as e:  # noqa: BLE001
            per_query.append({
                "chunk_id": chunk_id, "query": query, "error": str(e),
            })
            ranks.append(None)
            continue
        hit_ids = [h.chunk.id for h in hits]
        rank = None
        for i, hid in enumerate(hit_ids, start=1):
            if hid == chunk_id:
                rank = i
                break
        ranks.append(rank)
        same_doc = any(getattr(h.chunk, "source", None) == source for h in hits)
        if same_doc:
            same_doc_hits += 1
        per_query.append({
            "chunk_id": chunk_id,
            "document_id": doc_id,
            "source": source,
            "heading": heading,
            "query": query,
            "rank": rank,
            "top_ids": hit_ids[:top_k],
            "same_doc_hit": same_doc,
            "degraded": bool(getattr(stats, "degraded", False)),
        })

    metrics = metrics_from_ranks(ranks)
    metrics["same_doc_hit_rate"] = round(same_doc_hits / max(1, len(ranks)), 4)
    metrics["degraded"] = degraded
    if degraded_reason:
        metrics["degraded_reason"] = degraded_reason

    gate = {
        "min_self_recall@1": MIN_SELF_RECALL_AT_1,
        "min_self_recall@3": MIN_SELF_RECALL_AT_3,
        "min_mrr": MIN_MRR,
    }
    gate_passed = bool(
        metrics["SelfRecall@1"] >= MIN_SELF_RECALL_AT_1
        and metrics["SelfRecall@3"] >= MIN_SELF_RECALL_AT_3
        and metrics["MRR"] >= MIN_MRR
    )
    recommendations = _recommendations(metrics, health, s)
    engine = (
        getattr(embedder, "model_name", None) if embedder is not None
        else "bm25-only"
    )
    return {
        "engine": engine,
        "collection": collection,
        "health": health,
        "metrics": metrics,
        "per_query": per_query,
        "gate": gate,
        "gate_passed": gate_passed,
        "recommendations": recommendations,
        "degraded_reason": degraded_reason,
    }


def _recommendations(
    metrics: dict[str, Any],
    health: dict[str, Any],
    s: Settings,
) -> list[str]:
    recs: list[str] = []
    if health.get("dim_mismatch"):
        recs.append(
            f"向量库维度 {health.get('store_dim')} 与配置 {health.get('cfg_dim')} "
            "不一致，请执行 reindex 后再评估"
        )
    if health.get("empty_documents"):
        recs.append(
            f"有 {health['empty_documents']} 篇文档 0 分块，检查解析失败或空文件"
        )
    r1 = float(metrics.get("SelfRecall@1") or 0)
    mrr = float(metrics.get("MRR") or 0)
    if r1 < MIN_SELF_RECALL_AT_1 or mrr < MIN_MRR:
        misaligned = [
            name for name in (
                "query_instruction", "semantic_floor", "bm25_jieba_enabled",
            )
            if getattr(s, name, None) != RECOMMENDED_RETRIEVAL_PRESET.get(name)
        ]
        if misaligned:
            recs.append(
                "自检索偏弱，且以下字段未达推荐值："
                + ", ".join(misaligned)
                + " —— 建议 POST /v1/config/retrieval-recommended 或 "
                "`doc2mind config --recommended-retrieval`"
            )
        else:
            recs.append(
                "自检索仍偏弱（已用推荐配置）：检查嵌入模型是否与索引匹配、"
                "分块是否被截断，或用 `doc2mind reindex` 重建后复测"
            )
    if metrics.get("degraded"):
        recs.append(str(metrics.get("degraded_reason") or "检索已降级"))
    if not recs and float(metrics.get("SelfRecall@1") or 0) >= MIN_SELF_RECALL_AT_1:
        recs.append("自检索基线健康；若需更高精度可开启重排并在本库复测")
    return recs
