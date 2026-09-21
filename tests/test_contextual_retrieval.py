"""C2 上下文检索（文档摘要前缀）测试 — 前缀拼接 + store 摘要关联 + reindex 生效。"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.pipeline import _contextual_chunk_text, reindex_store
from doc2mind.core.store.sqlite_vec import StoredDocument, VectorStore

EMBEDDING_DIM = 8


# --- 单元：前缀拼接 ---
class TestContextualChunkText:
    def test_with_summary_prepends_prefix(self) -> None:
        out = _contextual_chunk_text("正文内容", "文档摘要说明")
        assert out == "[文档摘要]文档摘要说明\n正文内容"

    def test_without_summary_returns_original(self) -> None:
        body = "没有摘要的文档正文"
        assert _contextual_chunk_text(body, None) == body

    def test_blank_summary_returns_original(self) -> None:
        body = "正文"
        assert _contextual_chunk_text(body, "   \n ") == body


class _RecorderEmbedder:
    """记录被嵌入文本的假嵌入器：维度固定 8，恒定向量。"""

    def __init__(self) -> None:
        self.dimension = EMBEDDING_DIM
        self.model_name = "mock-embed"
        self.embedded_texts: list[str] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return [[1.0] * EMBEDDING_DIM for _ in texts]


class _VectorRecorderEmbedder:
    """记录被嵌入文本 + 返回随文本变化的向量，供 reindex 后用向量核对前缀。"""

    def __init__(self) -> None:
        self.dimension = EMBEDDING_DIM
        self.model_name = "mock-embed"
        self.embedded_texts: list[str] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.extend(texts)
        return [[1.0 if f"[文档摘要]" not in t else 2.0] * EMBEDDING_DIM for t in texts]


@pytest.fixture()
def store(tmp_path) -> Any:
    vs = VectorStore(tmp_path / "ctx.db", embedding_dim=EMBEDDING_DIM)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"sqlite-vec 不可用: {e}")
    yield vs
    vs.close()


def _add_doc(store: VectorStore, doc_id: str, summary: str | None) -> None:
    store.upsert_document(StoredDocument(
        id=doc_id, source=f"{doc_id}.md", collection="t", format="md",
        file_hash=f"hash-{doc_id}", size_bytes=100, page_count=None,
        chunk_count=1,
        created_at="2026-01-01T00:00:00+08:00",
        updated_at="2026-01-01T00:00:00+08:00",
    ))
    store.insert_chunks(
        document_id=doc_id, collection="t", source=f"{doc_id}.md", fmt="md",
        chunks=[Chunk(content=f"正文-{doc_id}", tokens=8,
                      metadata={"chunk_index": 0})],
        embeddings=[[1.0] * EMBEDDING_DIM],
    )
    if summary is not None:
        store.update_document_meta(doc_id, summary=summary)


# --- 存储层：list_chunk_contexts 关联文档摘要 ---
class TestListChunkContexts:
    def test_returns_summary_and_original_without(self, store) -> None:
        _add_doc(store, "a", summary="文档A摘要")
        _add_doc(store, "b", summary=None)  # 无摘要 → None
        ctx = store.list_chunk_contexts("t")
        assert len(ctx) == 2
        # 两条正文都被带到
        contents = {c for _, c, _ in ctx}
        assert contents == {"正文-a", "正文-b"}
        # 摘要仅关联到含摘要文档
        with_sum = {cid for cid, _, s in ctx if s == "文档A摘要"}
        assert len(with_sum) == 1

    def test_original_list_chunk_contents_unchanged(self, store) -> None:
        _add_doc(store, "a", summary="文档A摘要")
        pairs = store.list_chunk_contents("t")
        assert all(len(p) == 2 for p in pairs)  # (id, content)，不带摘要


# --- 流水线：reindex 按开关拼接前缀 ---
class TestReindexContextual:
    @staticmethod
    def _settings(db_path, contextual: bool):
        from doc2mind.core.config import Settings

        return Settings(
            db_path=str(db_path),
            contextual_retrieval=contextual,
            bm25_jieba_enabled=True,
            embed_batch_size=32,
        )

    def test_contextual_on_embeds_prefixed(self, store, tmp_path) -> None:
        """启用时：对含摘要文档嵌入 [文档摘要]<摘要>，无摘要文档退化为原文。"""
        _add_doc(store, "a", summary="气缸维修要点")
        _add_doc(store, "b", summary=None)
        rec = _VectorRecorderEmbedder()
        with patch("doc2mind.core.pipeline.get_embedder", return_value=rec):
            res = reindex_store(
                collection="t",
                settings=self._settings(tmp_path / "ctx.db", True),
            )
        assert res["contextual"] is True
        assert res["processed"] == 2
        assert any("[文档摘要]气缸维修要点" in t for t in rec.embedded_texts)
        assert "正文-b" in rec.embedded_texts            # 无摘要 → 原样
        assert "[文档摘要]气缸维修要点\n正文-a" in rec.embedded_texts  # 有摘要 → 前缀化
        assert "正文-a" not in rec.embedded_texts         # 绝不嵌入未带前缀的原文

    def test_contextual_off_embeds_original(self, store, tmp_path) -> None:
        """关闭时：走 list_chunk_contents，正文原样嵌入。"""
        _add_doc(store, "a", summary="任何摘要")
        rec = _RecorderEmbedder()
        with patch("doc2mind.core.pipeline.get_embedder", return_value=rec):
            res = reindex_store(
                collection="t",
                settings=self._settings(tmp_path / "ctx.db", False),
            )
        assert res["contextual"] is False
        assert rec.embedded_texts == ["正文-a"]