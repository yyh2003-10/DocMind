"""库状态 — 最新 / 待同步 / 索引过期 的统一可读报告。

用户不该心算「要不要 reindex」；本模块只回答三句话：
1. 库能不能用？
2. 索引是不是当前模型的？
3. 有没有需要一键修复的事？
"""

from __future__ import annotations

from typing import Any


def get_library_status(store: Any, settings: Any) -> dict[str, Any]:
    """返回库状态摘要（人话 + 可操作 action）。"""
    docs = []
    try:
        docs = store.list_documents(limit=10000) or []
    except Exception:
        docs = []

    # 去掉集合占位行
    real_docs = [
        d for d in docs
        if getattr(d, "source", "") != "__collection_placeholder__"
    ]
    total_docs = len(real_docs)
    total_chunks = sum(int(getattr(d, "chunk_count", 0) or 0) for d in real_docs)

    store_dim = getattr(store, "embedding_dim", None)
    cfg_dim = getattr(settings, "embed_dim", None)
    embed_model = getattr(settings, "embed_model", "")

    issues: list[dict[str, str]] = []
    status = "ok"

    if total_docs == 0:
        status = "empty"
        issues.append({
            "code": "empty",
            "level": "info",
            "message": "知识库还是空的，先导入文档或一键体验示例库",
            "action": "ingest",
        })
    else:
        # 维度不一致 = 换模型未重建
        if store_dim and cfg_dim and int(store_dim) != int(cfg_dim):
            status = "reindex_needed"
            issues.append({
                "code": "dim_mismatch",
                "level": "error",
                "message": (
                    f"向量库维度 {store_dim} 与当前嵌入模型配置 {cfg_dim} 不一致，"
                    "检索可能退化为纯关键词；请重建索引"
                ),
                "action": "reindex",
            })

        # 配置了长窗口但分块仍可能截断
        embed_max = int(getattr(settings, "embed_max_length", 512) or 512)
        chunk_max = int(getattr(settings, "chunk_max_tokens", 480) or 480)
        if chunk_max > embed_max:
            status = "warn" if status == "ok" else status
            issues.append({
                "code": "chunk_embed_mismatch",
                "level": "warn",
                "message": (
                    f"chunk_max_tokens={chunk_max} 大于 embed_max_length={embed_max}，"
                    "新导入的大块可能只嵌入前半截"
                ),
                "action": "align_config",
            })

        # contextual_retrieval 开启但多数文档无摘要 → 需 reindex 才齐
        if getattr(settings, "contextual_retrieval", False):
            with_summary = 0
            for d in real_docs:
                if getattr(d, "summary", None) or getattr(d, "title", None):
                    # title 不等于 summary；仅当 summary 非空才算
                    if getattr(d, "summary", None):
                        with_summary += 1
            if total_docs and with_summary < total_docs:
                if status == "ok":
                    status = "warn"
                issues.append({
                    "code": "contextual_pending",
                    "level": "info",
                    "message": (
                        f"contextual_retrieval 已开启，但仅 {with_summary}/{total_docs} "
                        "文档有摘要；执行重建索引后新摘要前缀才会生效"
                    ),
                    "action": "reindex",
                })

    # 分块/嵌入配置对齐提示（与入库健康同一套口径）
    embed_max = int(getattr(settings, "embed_max_length", 512) or 512)
    chunk_max = int(getattr(settings, "chunk_max_tokens", 480) or 480)

    return {
        "status": status,  # ok | empty | warn | reindex_needed
        "total_documents": total_docs,
        "total_chunks": total_chunks,
        "embed_model": embed_model,
        "embed_dim": cfg_dim,
        "store_dim": store_dim,
        "embed_max_length": embed_max,
        "chunk_max_tokens": chunk_max,
        "aligned": chunk_max <= embed_max,
        "issues": issues,
        "summary": _human_summary(status, total_docs, total_chunks, issues),
    }


def _human_summary(
    status: str,
    total_docs: int,
    total_chunks: int,
    issues: list[dict[str, str]],
) -> str:
    if status == "empty":
        return "库为空"
    if status == "reindex_needed":
        return f"需要重建索引（{total_docs} 文档 / {total_chunks} 分块）"
    if status == "warn":
        return f"可用但有提醒（{total_docs} 文档 / {total_chunks} 分块）"
    return f"最新可用（{total_docs} 文档 / {total_chunks} 分块）"
