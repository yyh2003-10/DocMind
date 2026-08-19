"""新手示例文档与快速体验模块测试。"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from unittest.mock import patch

import numpy as np

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.config import Settings
from doc2mind.core.embedder.base import Embedder
from doc2mind.core.sample_data import (
    SAMPLE_DOCUMENT_CONTENT,
    SAMPLE_DOCUMENT_TITLE,
    ingest_sample_knowledgebase,
)
from doc2mind.core.store.sqlite_vec import VectorStore


class MockEmbedder(Embedder):
    @property
    def model_name(self) -> str:
        return "mock-embedder"

    @property
    def dimension(self) -> int:
        return 8

    def embed(self, chunks: Sequence[Chunk]) -> Iterator[np.ndarray]:
        for _ in chunks:
            yield np.zeros(8, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return np.zeros(8, dtype=np.float32)


def test_sample_content_defined() -> None:
    assert len(SAMPLE_DOCUMENT_TITLE) > 0
    assert "DocMind" in SAMPLE_DOCUMENT_TITLE
    assert "混合检索" in SAMPLE_DOCUMENT_CONTENT
    assert "FastEmbed" in SAMPLE_DOCUMENT_CONTENT


def test_ingest_sample_knowledgebase_in_temp_db(tmp_path) -> None:
    db_file = tmp_path / "sample_test.db"
    settings = Settings(db_path=db_file, embed_dim=8)

    mock_emb = MockEmbedder()
    with patch("doc2mind.core.pipeline.get_embedder", return_value=mock_emb):
        res = ingest_sample_knowledgebase(collection="sample_test", settings=settings)

    assert res["ok"] is True
    assert res["status"] in ("ingested", "updated")
    assert res["chunk_count"] >= 1
    assert res["collection"] == "sample_test"

    # 验证数据库中确实写入了示例文档
    store = VectorStore(db_file, 8)
    store.open()
    try:
        docs = store.list_documents(collection="sample_test")
        assert len(docs) == 1
        assert docs[0].source == f"note:{SAMPLE_DOCUMENT_TITLE}"
    finally:
        store.close()
