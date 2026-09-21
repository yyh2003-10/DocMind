"""BM25 排序与 match_type 回归测试 — 真实临时 FTS5 库。

背景：SQLite FTS5 的 bm25() 返回负分（越小越相关），bm25_search 曾直接
ORDER BY score DESC，导致最不相关的命中排最前；负分再被上游
_bm25_normalize（score<=0→0）钳成 0，match_type 永远不出现 bm25/hybrid。
本文件锁定取反后的正确行为：分数为正、强相关排前、双路命中标 hybrid。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import doc2mind.server.mcp as mcp_server
from doc2mind.core.chunker.base import Chunk
from doc2mind.core.retriever.search import Retriever, _match_type
from doc2mind.core.store.sqlite_vec import StoredDocument, VectorStore

EMBEDDING_DIM = 8


class _FlatEmbedder:
    """恒定向量嵌入器：向量路对所有内容等距，隔离 BM25 相关断言。"""

    dimension = EMBEDDING_DIM

    def embed_query(self, text: str) -> list[float]:
        return [1.0] * EMBEDDING_DIM


def _add_doc(
    store: VectorStore, doc_id: str, collection: str, source: str,
    contents: list[str],
) -> None:
    store.upsert_document(StoredDocument(
        id=doc_id, source=source, collection=collection, format="md",
        file_hash=f"hash-{doc_id}", size_bytes=100, page_count=None,
        chunk_count=len(contents),
        created_at="2026-01-01T00:00:00+08:00",
        updated_at="2026-01-01T00:00:00+08:00",
    ))
    store.insert_chunks(
        document_id=doc_id, collection=collection, source=source, fmt="md",
        chunks=[Chunk(content=c, tokens=len(c), metadata={"chunk_index": i})
                for i, c in enumerate(contents)],
        embeddings=[[1.0] * EMBEDDING_DIM for _ in contents],
    )


@pytest.fixture()
def store(tmp_path) -> Any:
    vs = VectorStore(tmp_path / "bm25.db", embedding_dim=EMBEDDING_DIM)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"sqlite-vec 不可用: {e}")
    yield vs
    vs.close()


class TestBm25Search:
    def test_positive_scores_and_relevance_order(self, store) -> None:
        """分数必须为正；OR 语义下弱命中也入候选时，强相关必须排前。

        修复前：原始负分 DESC 排序把最弱命中排最前，且分数全为非正。
        """
        _add_doc(store, "d-strong", "t", "strong.md",
                 ["深度学习模型压缩与量化部署完整实战指南"])
        _add_doc(store, "d-weak", "t", "weak.md", ["量化入门小记"])

        hits = store.bm25_search("深度学习 模型压缩 量化", top_k=5, collection="t")
        assert hits, "BM25 应有命中"
        scores = [s for _, s in hits]
        assert all(s > 0 for s in scores), f"BM25 分数应为正，实际: {scores}"

        id_to_source = {c.id: c.source
                        for c in store.get_chunks([cid for cid, _ in hits])}
        order = [id_to_source[cid] for cid, _ in hits]
        assert "strong.md" in order
        if "weak.md" in order:
            assert order.index("strong.md") < order.index("weak.md")

    def test_short_cjk_word_recalled(self, store) -> None:
        """2 字中文词（气缸/电源/报警等）必须被 BM25 召回。

        修复前：trigram 分词器无法索引 <3 字符 token，`_build_fts5_match`
        回退的 `"词*"` 前缀查询不支持 trigram → 命中 0 行，混合检索静默
        退化为纯向量检索。
        """
        _add_doc(store, "d-1", "t", "cylinder.md",
                 ["气缸缸径 32mm，行程 200mm，配两位五通换向阀"])
        _add_doc(store, "d-2", "t", "filler.md", ["每日站会记录与待办事项"])

        hits = store.bm25_search("气缸", top_k=5, collection="t")
        assert hits, "2 字中文短词必须被 BM25 召回（LIKE 兜底）"
        id_to_source = {c.id: c.source
                        for c in store.get_chunks([cid for cid, _ in hits])}
        assert "cylinder.md" in id_to_source.values()
        cylinder_cid = next(c for c, s in id_to_source.items() if s == "cylinder.md")
        score = dict(hits)[cylinder_cid]
        assert score > 0, f"短词命中分应为正: {score}"

    def test_short_ascii_abbr_recalled(self, store) -> None:
        """2 字符英文/数字缩写（IP/5A/K型）必须被 BM25 召回。"""
        _add_doc(store, "d-1", "t", "ip.md",
                 ["设备支持 IP 防护等级 IP65，可户外安装"])
        _add_doc(store, "d-2", "t", "filler.md", ["今日会议安排不变"])

        hits = store.bm25_search("IP", top_k=5, collection="t")
        assert hits, "2 字符英文缩写必须被 BM25 召回（LIKE 兜底）"
        id_to_source = {c.id: c.source
                        for c in store.get_chunks([cid for cid, _ in hits])}
        assert "ip.md" in id_to_source.values()

    def test_short_token_scores_added_to_long_token_hits(self, store) -> None:
        """长词 + 短词混合查询：短词命中在同一分块上叠加贡献分，相关分块仍排前。"""
        _add_doc(store, "d-1", "t", "both.md",
                 ["伺服驱动器与气缸协同控制方案"])
        _add_doc(store, "d-2", "t", "long-only.md",
                 ["伺服驱动器选型与调试手册"])
        _add_doc(store, "d-3", "t", "short-only.md", ["气缸日常保养注意事项"])
        _add_doc(store, "d-4", "t", "filler.md", ["每日站会记录与待办事项"])

        hits = store.bm25_search("伺服驱动器 气缸", top_k=5, collection="t")
        assert hits
        id_to_source = {c.id: c.source
                        for c in store.get_chunks([cid for cid, _ in hits])}
        order = [id_to_source[cid] for cid, _ in hits]
        # 双信号命中的 both.md 应排在最前
        assert order[0] == "both.md", f"双信号命中应排最前: {order}"


class TestRetrieverSearch:
    def test_hits_carry_positive_bm25_score(self, store) -> None:
        """修复前关键词命中的 bm25_score 恒为 0（负分被钳零），必须带正分。

        注意 FTS5 特性：查询词命中全部文档时 IDF 被钳为 1e-6，因此
        语料必须包含不含查询词的填充文档，关键词命中才有实质分数。
        """
        _add_doc(store, "d-1", "t", "note.md", ["知识库支持混合检索与向量召回"])
        _add_doc(store, "d-2", "t", "filler.md", ["每日站会记录与待办事项"])

        retriever = Retriever(store=store, embedder=_FlatEmbedder())
        hits, _stats = retriever.search("混合检索 向量", collection="t", top_k=5)
        assert hits
        target = next(h for h in hits if h.chunk.source == "note.md")
        assert target.bm25_score > 0, f"关键词命中的 bm25_score 应为正: {target.bm25_score}"
        # 向量等距 → 所有 chunk 都是向量候选，BM25 命中即双路 hybrid
        assert target.match_type == "hybrid"

    def test_short_token_query_is_hybrid_not_vector(self, store) -> None:
        """只搜 2 字短词时，BM25 兜底命中必须让 match_type 呈现 hybrid
        而不是修复前的纯 vector（BM25 静默失效）。"""
        _add_doc(store, "d-1", "t", "cylinder.md",
                 ["气缸缸径 32mm，行程 200mm"])
        _add_doc(store, "d-2", "t", "filler.md", ["每日站会记录与待办事项"])

        retriever = Retriever(store=store, embedder=_FlatEmbedder())
        hits, _stats = retriever.search("气缸", collection="t", top_k=5)
        assert hits
        target = next(h for h in hits if h.chunk.source == "cylinder.md")
        assert target.bm25_score > 0, f"短词查询 bm25_score 应为正: {target.bm25_score}"
        assert target.match_type == "hybrid"


class TestMatchType:
    def test_classification(self) -> None:
        assert _match_type(0.9, 0.1) == "hybrid"
        assert _match_type(0.9, 0.0) == "vector"
        assert _match_type(0.0, 0.3) == "bm25"
        assert _match_type(0.0, 0.0) == "unknown"


class TestMcpSearchTool:
    @pytest.fixture()
    def mcp_env(self, tmp_path, monkeypatch) -> Any:
        """_tool_search 的 finally 会 close 返回的 store，因此替身
        _open_store 每次调用都重开同一临时库，写入库的连接单独管理。"""
        db_path = tmp_path / "mcp.db"
        writer = VectorStore(db_path, embedding_dim=EMBEDDING_DIM)
        try:
            writer.open()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"sqlite-vec 不可用: {e}")

        opened: list[VectorStore] = []

        def fake_open() -> tuple[VectorStore, Any]:
            vs = VectorStore(db_path, embedding_dim=EMBEDDING_DIM)
            vs.open()
            opened.append(vs)
            return vs, _FlatEmbedder()

        monkeypatch.setattr(mcp_server, "_open_store", fake_open)
        yield writer
        writer.close()
        for vs in opened:
            vs.close()

    def test_returns_engine_scores(self, mcp_env) -> None:
        """MCP 返回必须携带 vector_score / bm25_score，调用方才能判断相关性。"""
        _add_doc(mcp_env, "d-1", "t", "note.md", ["DocMind 知识库混合检索"])
        # 填充文档：避免查询词命中全部文档导致 FTS5 IDF 钳为 1e-6
        _add_doc(mcp_env, "d-2", "t", "filler-a.md", ["每日站会记录"])
        _add_doc(mcp_env, "d-3", "t", "filler-b.md", ["午餐菜单投票"])

        payload = json.loads(mcp_server._tool_search("混合检索", collection="t", top_k=5))
        hits = payload["result"]["hits"]
        assert hits
        for h in hits:
            assert "vector_score" in h and "bm25_score" in h
        target = next(h for h in hits if h["source"] == "note.md")
        assert target["bm25_score"] > 0
        assert target["match_type"] == "hybrid"

    def test_min_score_filters_weak_hits(self, mcp_env) -> None:
        """min_score 透传：卡在强弱命中之间时，弱命中被过滤、强命中保留。"""
        _add_doc(mcp_env, "d-1", "t", "a.md", ["Alpha 主题内容"])
        _add_doc(mcp_env, "d-2", "t", "b.md", ["Beta 另一个主题"])

        all_hits = json.loads(
            mcp_server._tool_search("Alpha", collection="t", top_k=10)
        )["result"]["hits"]
        assert len(all_hits) == 2  # 向量路等距，两个 chunk 都进候选
        scores = sorted((h["score"] for h in all_hits), reverse=True)
        midpoint = (scores[0] + scores[1]) / 2

        filtered = json.loads(
            mcp_server._tool_search("Alpha", collection="t", top_k=10,
                                    min_score=midpoint)
        )["result"]["hits"]
        assert [h["source"] for h in filtered] == ["a.md"]
