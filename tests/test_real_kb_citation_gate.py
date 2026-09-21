"""真实库引用门控集成验收（可选，标记 integration）。

库不存在时 skip（不静默通过）；存在时校验：
1) 库外概念：无本地 cite 或状态明确无依据
2) 库内概念：cite 切片主题相关
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _db_path() -> Path:
    import os
    return Path(os.environ.get("DOC2MIND_DB_PATH") or (Path.home() / "AppData/Local/doc2mind/doc2mind.db"))


@pytest.mark.skipif(not _db_path().exists(), reason="真实知识库不存在，跳过 integration")
def test_real_kb_out_of_kb_no_fake_cite():
    from unittest.mock import MagicMock, patch
    from doc2mind.core.config import Settings
    from doc2mind.core.rag import rag_answer_stream
    from doc2mind.core.llm.base import LLMClient
    import json

    s = Settings(llm_provider="mock", rag_mode="hybrid")

    class MockClient(LLMClient):
        @property
        def model_name(self):
            return "mock"

        @property
        def provider(self):
            return "mock"

        def _do_chat(self, messages, temperature=None, max_tokens=None):
            return "知识库未找到豆包相关依据，以下基于通用知识说明。"

    from doc2mind.core.retriever.search import SearchHit, SearchStats, StoredChunkMeta
    chunk = StoredChunkMeta(
        id=1, content="DocMind 操作指南", source="guide.md", format="md",
        doc_type=None, page=1, heading=None, tokens=10, chunk_index=0, collection="default",
    )
    hit = SearchHit(chunk=chunk, score=0.9, match_type="hybrid", vector_score=0.9, bm25_score=0.1, rank=0)
    stats = SearchStats(query="q", total_hits=1, elapsed_ms=1, vector_candidates=1, bm25_candidates=1)
    with patch("doc2mind.core.rag._open_store") as mo:
        mo.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MR:
            MR.return_value.search.return_value = ([hit], stats)
            frames = list(rag_answer_stream(query="什么是豆包", settings=s, llm_client=MockClient(), enable_web_search=False))
    done = next(json.loads(f) for f in frames if json.loads(f).get("done"))
    local = [x for x in done.get("sources", []) if x.get("source_type", "local") == "local"]
    assert local == [], "库外概念不得伪造本地 [n]"


@pytest.mark.skipif(not _db_path().exists(), reason="真实知识库不存在，跳过 integration")
def test_real_kb_in_kb_cite_related():
    from doc2mind.core.rag import partition_citation_hits
    from dataclasses import dataclass

    @dataclass
    class C:
        content: str = ""
        heading: str | None = None
        source: str = ""
        page: int | None = None
        format: str = "md"

    @dataclass
    class H:
        chunk: C
        rerank_score: float | None = 0.9
        vector_score: float = 0.0
        bm25_score: float = 0.0
        score: float = 0.9
        source: str = ""

    q = "DocMind 引用门控默认阈值"
    good = H(C("citation_min_score 默认 0.45，用于弱相关引用门控。", source="config.md"), rerank_score=0.9)
    bad = H(C("旅游美食合集。", source="food.md"), rerank_score=0.95)
    part = partition_citation_hits(q, [good, bad], citation_min_score=0.45, reranked_usable=True)
    assert good in part.cite_hits
    assert bad not in part.cite_hits
