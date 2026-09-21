"""F0/F1：推荐检索配置 + 本库评估 — 回归测试。"""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.config import (
    BGE_ZH_QUERY_INSTRUCTION,
    RECOMMENDED_RETRIEVAL_FIELDS,
    RECOMMENDED_RETRIEVAL_PRESET,
    Settings,
    apply_recommended_retrieval,
    recommended_retrieval_preview,
)
from doc2mind.core.eval_library import (
    MIN_SELF_RECALL_AT_1,
    evaluate_library,
    metrics_from_ranks,
    pick_query_from_content,
)
from doc2mind.core.store.sqlite_vec import StoredDocument, VectorStore


class _UnitEmbedder:
    """确定性 unit 向量：按 crc32(text) 选维度（跨进程稳定）。"""

    dimension = 8

    def embed_query(self, text: str) -> list[float]:
        v = [0.0] * self.dimension
        v[zlib.crc32(text.encode("utf-8")) % self.dimension] = 1.0
        return v

    def embed_text(self, text: str) -> list[float]:
        return self.embed_query(text)


def test_recommended_preset_fields_match_whitelist() -> None:
    for name in RECOMMENDED_RETRIEVAL_FIELDS:
        assert name in RECOMMENDED_RETRIEVAL_PRESET, name
    assert RECOMMENDED_RETRIEVAL_PRESET["query_instruction"] == BGE_ZH_QUERY_INSTRUCTION
    assert RECOMMENDED_RETRIEVAL_PRESET["semantic_floor"] > 0
    assert RECOMMENDED_RETRIEVAL_PRESET["bm25_jieba_enabled"] is True


def test_preview_reports_misaligned_defaults() -> None:
    s = Settings()
    # 出厂默认故意保守：query_instruction 为空、floor=0
    preview = recommended_retrieval_preview(s)
    assert preview["aligned"] is False
    fields = {c["field"] for c in preview["changes"]}
    assert "query_instruction" in fields
    assert "semantic_floor" in fields


def test_apply_recommended_updates_and_is_idempotent() -> None:
    s = Settings()
    updated, changes = apply_recommended_retrieval(s)
    assert changes
    assert updated.query_instruction == BGE_ZH_QUERY_INSTRUCTION
    assert updated.semantic_floor == RECOMMENDED_RETRIEVAL_PRESET["semantic_floor"]
    assert updated.bm25_jieba_enabled is True
    # 再应用一次：无变更
    again, changes2 = apply_recommended_retrieval(updated)
    assert changes2 == []
    assert again.query_instruction == updated.query_instruction
    preview = recommended_retrieval_preview(updated)
    assert preview["aligned"] is True


def test_apply_recommended_fields_subset() -> None:
    s = Settings()
    updated, changes = apply_recommended_retrieval(s, fields=["semantic_floor"])
    assert len(changes) == 1
    assert changes[0]["field"] == "semantic_floor"
    # 其余保持出厂
    assert updated.query_instruction == s.query_instruction


def test_pick_query_from_content() -> None:
    q = pick_query_from_content("气缸缸径为 50mm。后半段还有内容。")
    assert "气缸" in q
    assert len(q) <= 80
    assert pick_query_from_content("   ") == ""


def test_metrics_from_ranks() -> None:
    m = metrics_from_ranks([1, 2, None, 3])
    assert m["n_queries"] == 4
    assert m["SelfRecall@1"] == 0.25
    assert m["SelfRecall@3"] == 0.75
    assert abs(m["MRR"] - round((1 + 0.5 + 0 + 1 / 3) / 4, 4)) < 1e-9


def _make_store(tmp_path: Path, dim: int = 8) -> VectorStore:
    store = VectorStore(tmp_path / "eval.db", dim, bm25_jieba_enabled=True)
    store.open()
    return store


def _insert(store: VectorStore, emb: _UnitEmbedder, doc_id: str, source: str,
            collection: str, chunks: list[str]) -> None:
    store.upsert_document(
        StoredDocument(
            id=doc_id,
            source=source,
            collection=collection,
            format="md",
            file_hash=doc_id,
            size_bytes=100,
            page_count=1,
            chunk_count=len(chunks),
            created_at="2026-01-01T00:00:00+08:00",
            updated_at="2026-01-01T00:00:00+08:00",
        )
    )
    store.insert_chunks(
        document_id=doc_id,
        collection=collection,
        source=source,
        fmt="md",
        chunks=[
            Chunk(content=text, tokens=len(text), metadata={"chunk_index": i})
            for i, text in enumerate(chunks)
        ],
        embeddings=[emb.embed_text(t) for t in chunks],
    )


def test_evaluate_library_empty(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    try:
        report = evaluate_library(store, None, sample=5, seed=1)
        assert report["gate_passed"] is False
        assert report["degraded_reason"] == "empty_library"
    finally:
        store.close()


def test_evaluate_library_self_retrieval(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    emb = _UnitEmbedder()
    try:
        _insert(
            store, emb, "d1", "a.md", "default",
            [
                "DocMind 使用 sqlite-vec 做本地向量检索，并配合 BM25 混合召回。",
                "PaddleOCR 用于扫描型 PDF 与图片的文字识别，需要单独安装 extras。",
                "MCP 工具可以把知识库暴露给 Claude Code 等外部 agent 调用。",
            ],
        )
        _insert(
            store, emb, "d2", "b.md", "default",
            [
                "推荐检索配置包含 bge 中文查询指令与语义下限 semantic_floor。",
                "文件监控 watchdog 监听目录变更并自动触发摄入流水线。",
            ],
        )
        s = Settings()
        report = evaluate_library(
            store, emb, sample=10, seed=7, top_k=5, settings=s,
        )
        assert report["health"]["total_documents"] == 2
        assert report["metrics"]["n_queries"] >= 3
        # unit 向量 + 同文本查询应能自检索命中（BM25 也强）
        assert report["metrics"]["SelfRecall@1"] >= MIN_SELF_RECALL_AT_1
        assert report["gate_passed"] is True or report["metrics"]["SelfRecall@1"] > 0
        # 确定性：同 seed 结果一致
        report2 = evaluate_library(
            store, emb, sample=10, seed=7, top_k=5, settings=s,
        )
        assert report["metrics"] == report2["metrics"]
    finally:
        store.close()


def test_evaluate_library_bm25_only_degraded(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    emb = _UnitEmbedder()
    try:
        _insert(
            store, emb, "d1", "a.md", "default",
            ["知识图谱实体抽取依赖 LLM 与 graph_store 落库。"],
        )
        report = evaluate_library(store, None, sample=5, seed=3)
        assert report["metrics"]["degraded"] is True
        assert "BM25" in (report["metrics"].get("degraded_reason") or "")
    finally:
        store.close()
