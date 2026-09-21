"""检索根基升级回归测试。

覆盖：
- A2 忠实 距离→相似度 映射（根因：旧公式把真实余弦 0.33 虚高显示成 0.60）
- A7 评估件（tools/eval_retrieval.py）MRR/Recall@k/标定/“60+”回归
- A4 jieba 中文 BM25：2 字中文词经 unicode61 召回，不依赖 trigram LIKE 兜底
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# 使 tools/ 可导入（仅内部测试依赖，不安装为包）
_TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import eval_retrieval

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.retriever.search import (
    Retriever,
    StoredChunkMeta,
    SearchHit,
    _distance_to_score,
)
from doc2mind.core.store.sqlite_vec import StoredDocument, VectorStore

EMBEDDING_DIM = 8


class _FlatEmbedder:
    """恒定向量嵌入器：向量路对所有内容等距，排序由 BM25 决定（便于预测）。"""

    dimension = EMBEDDING_DIM

    def embed_query(self, text: str) -> list[float]:
        return [1.0] * EMBEDDING_DIM


def _unit_vec(idx: int, dim: int = EMBEDDING_DIM) -> list[float]:
    """仅第 idx 维为 1 的 unit 向量（与 eval_retrieval._unit 同构，测试侧独立）。"""
    v = [0.0] * dim
    v[idx % dim] = 1.0
    return v


class TestFaithfulDistanceToScore:
    def test_exact_mapping(self) -> None:
        assert _distance_to_score(0.0) == pytest.approx(1.0)
        assert _distance_to_score(0.67) == pytest.approx(0.33, abs=1e-6)
        assert _distance_to_score(1.0) == pytest.approx(0.0)
        assert _distance_to_score(2.0) == pytest.approx(0.0)
        # 异常负数钳制为 1.0
        assert _distance_to_score(-0.5) == pytest.approx(1.0)

    def test_monotonic_decreasing(self) -> None:
        assert _distance_to_score(0.1) > _distance_to_score(0.7) > _distance_to_score(1.0)

    def test_60plus_regression(self) -> None:
        """真实余弦 0.33 的负样本：display score 必须 < 0.5 且 == 1 - distance。

        旧公式 1/(1+d)：d=0.67 → 0.599 → 界面显示“相关度 60+”，数据污染根因。
        """
        distance = 1.0 - eval_retrieval.NEGATIVE_COS  # 0.67
        assert distance == pytest.approx(0.67)
        old = 1.0 / (1.0 + distance)
        assert old == pytest.approx(0.599, abs=1e-3)  # 虚高
        new = _distance_to_score(distance)
        assert new == pytest.approx(eval_retrieval.NEGATIVE_COS, abs=1e-6)
        assert new < 0.5


class TestEvalHarness:
    def test_mrr_recall_baseline(self) -> None:
        r = eval_retrieval.evaluate()
        m = r["metrics"]
        assert m["MRR"] == pytest.approx(1.0)
        assert m["Recall@1"] == pytest.approx(1.0)
        assert m["Recall@3"] == pytest.approx(1.0)
        assert m["Recall@5"] == pytest.approx(1.0)

    def test_calibration(self) -> None:
        r = eval_retrieval.evaluate()
        c = r["calibration"]
        assert c["negative_count"] > 0
        # 负样本显示的 vector_score 均值显著低于相关阈值 0.30
        assert c["negative_mean_vector_score"] < 0.30

    def test_60plus_regression_through_end_to_end_search(self) -> None:
        reg = eval_retrieval.evaluate()["regression_60plus"]
        assert reg["regression_fixed"] is True
        assert reg["displayed_vector_score"] < 0.5
        assert reg["maps_to_1_minus_distance"] is True
        # 与真实余弦一致
        assert reg["displayed_vector_score"] == pytest.approx(
            eval_retrieval.NEGATIVE_COS, abs=1e-2
        )


class TestBm25JiebaShortCjk:
    def test_two_char_cjk_recalled_via_unicode61(self, tmp_path) -> None:
        """2 字中文词（气缸）在 bm25_jieba_enabled=True 时经 unicode61 召回。

        依赖 jieba 分词而非 trigram 的 LIKE 兜底 —— 锁定 A4 中文召回路径。
        """
        store = VectorStore(
            tmp_path / "jieba.db", embedding_dim=EMBEDDING_DIM, bm25_jieba_enabled=True
        )
        try:
            store.open()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"sqlite-vec 不可用: {e}")

        store.upsert_document(StoredDocument(
            id="d", source="cylinder.md", collection="t", format="md",
            file_hash="h", size_bytes=100, page_count=None, chunk_count=1,
            created_at="2026-01-01T00:00:00+08:00",
            updated_at="2026-01-01T00:00:00+08:00",
        ))
        store.insert_chunks(
            document_id="d", collection="t", source="cylinder.md", fmt="md",
            chunks=[Chunk(content="气缸缸径 32mm，行程 200mm，配两位五通换向阀",
                          tokens=16, metadata={"chunk_index": 0})],
            embeddings=[[1.0] * EMBEDDING_DIM],
        )
        try:
            hits = store.bm25_search("气缸", top_k=5, collection="t")
            assert hits, "jieba+unicode61 必须召回 2 字中文词（不依赖 LIKE 兜底）"
            id_to_source = {c.id: c.source
                            for c in store.get_chunks([cid for cid, _ in hits])}
            assert "cylinder.md" in id_to_source.values()
        finally:
            store.close()

    def test_jieba_segment_toggle(self) -> None:
        # 开关影响分词输出：开 → 空格拼接，关 → 原样返回
        assert eval_retrieval._distance_to_score  # ensure module import stable
        from doc2mind.core.store.tokenizer import segment
        assert segment("气缸故障", True) != "气缸故障"  # jieba 分词后含空格
        assert segment("气缸故障", False) == "气缸故障"


class TestB2NeighborContext:
    def _store_with_chunks(self, tmp_path) -> tuple[VectorStore, list[int]]:
        store = VectorStore(tmp_path / "nb.db", embedding_dim=EMBEDDING_DIM)
        try:
            store.open()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"sqlite-vec 不可用: {e}")
        store.upsert_document(StoredDocument(
            id="d1", source="doc.md", collection="t", format="md",
            file_hash="h", size_bytes=100, page_count=None, chunk_count=4,
            created_at="2026-01-01T00:00:00+08:00",
            updated_at="2026-01-01T00:00:00+08:00",
        ))
        contents = [f"segment-{i} content" for i in range(4)]
        store.insert_chunks(
            document_id="d1", collection="t", source="doc.md", fmt="md",
            chunks=[Chunk(content=c, tokens=len(c), metadata={"chunk_index": i})
                    for i, c in enumerate(contents)],
            embeddings=[[1.0] * EMBEDDING_DIM for _ in contents],
        )
        ids = [cid for cid, _ in store.list_chunk_contents()]
        return store, ids

    def test_get_neighbor_chunks_window(self, tmp_path) -> None:
        store, ids = self._store_with_chunks(tmp_path)
        try:
            # 命中第 2 块（index=1），window=1 → 前后各 1 块，不含自身
            mid = ids[1]
            nb = store.get_neighbor_chunks(mid, window=1)
            assert [c.chunk_index for c in nb] == [0, 2]
            # window=0 关闭
            assert store.get_neighbor_chunks(mid, window=0) == []
            # 边界：首块无前邻，只有后邻
            first = store.get_neighbor_chunks(ids[0], window=1)
            assert [c.chunk_index for c in first] == [1]
        finally:
            store.close()

    def test_format_context_appends_neighbors(self, tmp_path) -> None:
        from doc2mind.core.rag import _format_context

        store, ids = self._store_with_chunks(tmp_path)
        try:
            mid_id = ids[1]
            meta = StoredChunkMeta(
                id=mid_id, content="segment-1 content", source="doc.md",
                format="md", doc_type=None, page=None, heading=None,
                tokens=6, chunk_index=1, collection="t",
            )
            hit = SearchHit(
                chunk=meta, score=0.02, match_type="vector",
                vector_score=0.9, bm25_score=0.0, rank=0,
            )
            # 默认（无 store / window=0）：不追加邻块
            ctx_off, _ = _format_context([hit])
            assert "相邻上下文" not in ctx_off
            # 开启邻块上下文：并入同源相邻分块
            ctx_on, _ = _format_context([hit], store=store, neighbor_window=1)
            assert "相邻上下文" in ctx_on
            assert "segment-0 content" in ctx_on
            assert "segment-2 content" in ctx_on
        finally:
            store.close()


class TestB1EmbedModelPresets:
    def test_resolve_alias_to_canonical(self) -> None:
        from doc2mind.core.embedder.catalog import resolve_embed_model

        assert resolve_embed_model("bge-en-large") == "BAAI/bge-large-en-v1.5"
        assert resolve_embed_model("bge-small-zh") == "BAAI/bge-small-zh-v1.5"
        # 大小写不敏感
        assert resolve_embed_model("BGE-EN-BASE") == "BAAI/bge-base-en-v1.5"

    def test_resolve_non_alias_passthrough(self) -> None:
        from doc2mind.core.embedder.catalog import resolve_embed_model

        # 完整名直接返回
        assert resolve_embed_model("BAAI/bge-small-en-v1.5") == "BAAI/bge-small-en-v1.5"
        # 自定义/未知模型原样返回（允许用户自定义）
        assert resolve_embed_model("my-custom-model") == "my-custom-model"
        # 空值安全
        assert resolve_embed_model("") == ""

    def test_every_preset_is_supported_and_dim_known(self) -> None:
        """B1 零风险：每个预设别名都解析到受支持清单里的模型，且维度已知。"""
        from doc2mind.core.embedder.catalog import (
            EMBED_MODEL_PRESETS,
            get_model_info,
        )

        assert EMBED_MODEL_PRESETS, "至少应有窄快预设"
        for alias, canonical in EMBED_MODEL_PRESETS.items():
            info = get_model_info(canonical)
            assert info is not None, (
                f"预设 {alias} -> {canonical} 不在受支持清单中（会加载失败）"
            )
            assert info.dim > 0

    def test_get_model_info_resolves_alias(self) -> None:
        from doc2mind.core.embedder.catalog import get_model_info

        assert get_model_info("bge-en-base").name == "BAAI/bge-base-en-v1.5"

    def test_fastembed_resolves_alias_and_syncs_dim(self) -> None:
        from dataclasses import replace

        from doc2mind.core.config import Settings
        from doc2mind.core.embedder.fastembed_impl import FastEmbedEmbedder

        s = Settings()
        # 配置里用别名 + 旧维度 512，构造时应解析为完整名并把维度同步为 1024
        e = FastEmbedEmbedder(replace(s, embed_model="bge-en-large", embed_dim=512))
        assert e.model_name == "BAAI/bge-large-en-v1.5"
        assert e.dimension == 1024
        # 默认预设仍是 bge-small-zh（512 维），对新安装零风险
        default = FastEmbedEmbedder(s)
        assert default.model_name == "BAAI/bge-small-zh-v1.5"
        assert default.dimension == 512

    def test_eval_real_model_not_supported_marker(self) -> None:
        """真实模型（无受控夹角）模式下不输出 60+ 回归断言。"""
        class _StubEmbedder:
            """仅实现 evaluate(real) 所需接口：dimension / embed_text / embed_query。"""
            dimension = 8

            def embed_text(self, text: str) -> list[float]:
                # 确定性伪向量（按内容 hash），避免真实模型依赖
                return [float(ord(c) % 7) / 7.0 for c in text.ljust(8)][:8]

            def embed_query(self, text: str) -> list[float]:
                return self.embed_text(text)

        r = eval_retrieval.evaluate(embedder=_StubEmbedder())
        assert r["regression_60plus"] is None
        assert r["metrics"]["MRR"] >= 0
        assert r["metrics"]["Recall@1"] >= 0


class TestB3FusionRerankTuning:
    """B3 融合与重排调优：加权融合 / 分数融合 / 动态重排候选 / 校准温度。"""

    def test_parse_rrf_weights(self) -> None:
        from doc2mind.core.config import parse_rrf_weights

        # 两位 → (vec, bm25, sparse=0)；三位 → 全给（D2 稀疏路）
        assert parse_rrf_weights("2,1") == (2.0, 1.0, 0.0)
        assert parse_rrf_weights("2,1,1") == (2.0, 1.0, 1.0)
        assert parse_rrf_weights("0,3") == (0.0, 3.0, 0.0)  # 负值钳为 0
        assert parse_rrf_weights("") == (1.0, 1.0, 0.0)  # 非法回退中性
        assert parse_rrf_weights("abc") == (1.0, 1.0, 0.0)

    def test_rrf_weights_reorder(self) -> None:
        """赋权向量路后，向量高排名的 chunk 融合分应上浮。"""
        from doc2mind.core.retriever.search import _rrf_fuse

        vec = [(1, 1.0), (2, 0.9), (3, 0.8)]
        bm = [(3, 0.9), (2, 0.8), (1, 0.1)]
        neutral = _rrf_fuse(vec, bm, k=60, weights=(1.0, 1.0))
        wvec = _rrf_fuse(vec, bm, k=60, weights=(5.0, 1.0))
        neutral_map = {c: s for c, s, _, _ in neutral}
        wvec_map = {c: s for c, s, _, _ in wvec}
        # 向量权重拉满后，向量第 1 的 chunk1 大幅领先
        assert wvec_map[1] > neutral_map[1]
        # chunk1 向量第1、BM25第3：向量权重大 → 应为融合分最高
        top = max(wvec, key=lambda x: x[1])
        assert top[0] == 1

    def test_score_fuse_math(self) -> None:
        from doc2mind.core.retriever.search import _score_fuse

        vec = [(1, 0.9), (2, 0.6)]
        bm = [(2, 0.5), (3, 0.8)]
        out = _score_fuse(vec, bm, weights=(1.0, 1.0))
        m = {c: s for c, s, v, b in out}
        assert m[1] == pytest.approx(0.9)  # 仅向量路
        assert m[2] == pytest.approx(1.1)  # 0.6 + 0.5 两路
        assert m[3] == pytest.approx(0.8)  # 仅 BM25 路
        # 加权：向量权重 2
        w = {c: s for c, s, _, _ in _score_fuse(vec, bm, weights=(2.0, 1.0))}
        assert w[1] == pytest.approx(1.8)
        assert w[2] == pytest.approx(1.7)  # 2*0.6 + 0.5

    def test_fuse_dispatch(self) -> None:
        from doc2mind.core.retriever.search import _fuse

        vec = [(1, 1.0), (2, 0.5)]
        bm = [(2, 0.5), (3, 0.9)]
        # rrf 模式产出倒数排名分（量纲小）；score 模式产出加权分数
        rrf = _fuse(mode="rrf", k=60, vec_ranking=vec, bm25_ranking=bm)
        score = _fuse(mode="score", k=60, vec_ranking=vec, bm25_ranking=bm, weights=(1, 1))
        assert max(r for _, r, _, _ in rrf) < 0.05  # RRF 量纲小
        assert max(s for _, s, _, _ in score) <= 2.0  # 分数融合 ≤ 权重和

    def test_sigmoid_temperature(self) -> None:
        from doc2mind.core.retriever.search import _sigmoid

        # 温度 1.0 = 原 plain sigmoid（向后兼容）
        assert _sigmoid(0.0) == pytest.approx(0.5)
        assert _sigmoid(5.0) == pytest.approx(_sigmoid(5.0, temperature=1.0))
        # 0<T<1 更陡：同 logit 下逼近 0/1 更快
        mid = 2.0
        assert _sigmoid(mid, temperature=0.5) > _sigmoid(mid, temperature=1.0)
        # >1 更平缓（更保守）
        assert _sigmoid(mid, temperature=2.0) < _sigmoid(mid, temperature=1.0)
        # 保持 [0,1] 与单调
        assert 0.0 <= _sigmoid(-100.0, temperature=0.5) <= 1.0
        assert _sigmoid(1.0, temperature=0.3) < _sigmoid(2.0, temperature=0.3)

    def test_dynamic_rerank_recall(self, tmp_path) -> None:
        """重排候选数应随 top_k 放大（max(配置值, top_k*3)），非固定配置值。"""
        from doc2mind.core.reranker.base import Reranker

        seen: list[int] = []

        class _CountingReranker(Reranker):
            @property
            def model_name(self) -> str:
                return "counting-reranker"

            def rerank(self, query, documents, batch_size=32):
                seen.append(len(documents))
                # 给每个候选不同的分（按位置递减），确保都拿到非 None rerank_score
                return [1.0 - i * 0.01 for i in range(len(documents))]

        vs = VectorStore(tmp_path / "b3.db", embedding_dim=EMBEDDING_DIM)
        try:
            vs.open()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"sqlite-vec 不可用: {e}")
        for i in range(8):
            store_doc = StoredDocument(
                id=f"d{i}", source=f"s{i}.md", collection="t", format="md",
                file_hash=f"h{i}", size_bytes=100, page_count=None, chunk_count=1,
                created_at="2026-01-01T00:00:00+08:00",
                updated_at="2026-01-01T00:00:00+08:00",
            )
            vs.upsert_document(store_doc)
            vs.insert_chunks(
                document_id=f"d{i}", collection="t", source=f"s{i}.md", fmt="md",
                chunks=[Chunk(content=f"keyword{i} text", tokens=2,
                              metadata={"chunk_index": 0})],
                embeddings=[[1.0] * EMBEDDING_DIM],
            )
        try:
            # rerank_recall=2（很小），但 top_k=5 → 动态放大 max(2, 15)=15，
            # 可用候选 8 个 → recall 应为 8，而非 2。
            retriever = Retriever(
                store=vs, embedder=_FlatEmbedder(), reranker=_CountingReranker(),
                rerank_recall=2,
            )
            hits, stats = retriever.search("keyword3", collection="t", top_k=5)
            assert stats.reranked is True
            assert seen and seen[-1] == 8, (
                f"期望重排候选数随 top_k 放大到全部候选(8)，实际 {seen}"
            )
            assert all(h.rerank_score is not None for h in hits)
        finally:
            vs.close()

    def test_fusion_mode_score_search(self, tmp_path) -> None:
        """fusion_mode="score" 端到端不崩溃，且按加权分数（vector+bm25）排序。"""
        vs = VectorStore(tmp_path / "b3s.db", embedding_dim=EMBEDDING_DIM)
        try:
            vs.open()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"sqlite-vec 不可用: {e}")
        for i in range(3):
            vs.upsert_document(StoredDocument(
                id=f"d{i}", source=f"s{i}.md", collection="t", format="md",
                file_hash=f"h{i}", size_bytes=100, page_count=None, chunk_count=1,
                created_at="2026-01-01T00:00:00+08:00",
                updated_at="2026-01-01T00:00:00+08:00",
            ))
            vs.insert_chunks(
                document_id=f"d{i}", collection="t", source=f"s{i}.md", fmt="md",
                chunks=[Chunk(content=f"苹果 keyword{i}", tokens=2,
                              metadata={"chunk_index": 0})],
                embeddings=[[1.0] * EMBEDDING_DIM],
            )
        try:
            retriever = Retriever(
                store=vs, embedder=_FlatEmbedder(), fusion_mode="score",
            )
            hits, stats = retriever.search("苹果", collection="t", top_k=3)
            assert stats.reranked is False
            assert len(hits) >= 1
            fused = [h.vector_score + h.bm25_score for h in hits]
            assert fused == sorted(fused, reverse=True)  # 按加权分数降序
        finally:
            vs.close()


class TestD1ChineseEndToEnd:
    """D1 中文端到端基准：真实中文文本贯通 jieba+BM25+RRF，可确定性回归。"""

    def test_baseline_passes_gates(self) -> None:
        r = eval_retrieval.evaluate_zh()
        m = r["metrics"]
        g = r["gates"]
        assert r["gate_passed"] is True
        assert m["MRR"] >= g["min_mrr"]
        assert m["Recall@1"] >= g["min_recall@1"]
        assert m["Recall@3"] >= g["min_recall@3"]
        assert r["engine"] == "text-hash-zh(deterministic)"

    def test_corpus_size_and_keys(self) -> None:
        r = eval_retrieval.evaluate_zh()
        assert len(r["per_query"]) == 7  # 7 条中文查询
        assert set(r["metrics"]) == {"MRR", "Recall@1", "Recall@3", "Recall@5"}
        assert set(r["gates"]) == {"min_mrr", "min_recall@1", "min_recall@3"}

    def test_deterministic_cross_run_stable(self) -> None:
        """crc32 token 向量跨运行稳定（PYTHONHASHSEED 无关），供 CI 锁回归。"""
        a = eval_retrieval.evaluate_zh()
        b = eval_retrieval.evaluate_zh()
        assert a["metrics"] == b["metrics"]
        assert [p["keys"] for p in a["per_query"]] == [p["keys"] for p in b["per_query"]]

    def test_real_model_marker_with_stub(self) -> None:
        """传入真实 embedder 走 A/B：产出 engine 名、算指标，不上门槛断言。"""

        class _Stub:
            dimension = 512
            model_name = "stub-zh"

            def embed_text(self, t: str) -> list[float]:
                return eval_retrieval._token_vec(t)

            def embed_query(self, t: str) -> list[float]:
                return eval_retrieval._token_vec(t)

        r = eval_retrieval.evaluate_zh(embedder=_Stub())
        assert r["engine"] == "stub-zh"
        assert r["metrics"]["MRR"] >= 0
        assert len(r["per_query"]) == 7


class TestB4VectorQuantize:
    """B4 大语料向量索引量化：默认 FLOAT；int8 请求在存储不支持时安全回退。"""

    def test_default_is_float_storage(self, tmp_path) -> None:
        """默认 vector_quantize="none" → vec_chunks 为 FLOAT 列（向后兼容）。"""
        vs = VectorStore(tmp_path / "b4.db", embedding_dim=EMBEDDING_DIM)
        try:
            vs.open()
            assert vs._vec_storage_type == "FLOAT"
            row = vs._conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='vec_chunks'"
            ).fetchone()
            assert isinstance(row, tuple) and "FLOAT" in row[0]
            assert "INT8" not in row[0]
        finally:
            vs.close()

    def test_int8_request_falls_back_when_unsupported(self, tmp_path) -> None:
        """本机 sqlite-vec 不支持 INT8 写入时：请求 int8 也不崩溃，回退 FLOAT。

        实测 sqlite-vec 0.1.9 wheel 的 INT8 列插入被一律按 float32 解释而报错，
        因此该路径应零风险回退，检索照常工作。
        """
        vs = VectorStore(
            tmp_path / "b4i.db", embedding_dim=EMBEDDING_DIM, vector_quantize="int8"
        )
        try:
            vs.open()
            assert vs._vec_storage_type == "FLOAT"
            vs.upsert_document(StoredDocument(
                id="d0", source="s.md", collection="t", format="md",
                file_hash="h0", size_bytes=10, page_count=None, chunk_count=1,
                created_at="2026-01-01T00:00:00+08:00",
                updated_at="2026-01-01T00:00:00+08:00",
            ))
            vs.insert_chunks(
                document_id="d0", collection="t", source="s.md", fmt="md",
                chunks=[Chunk(content="量化测试", tokens=2,
                              metadata={"chunk_index": 0})],
                embeddings=[[1.0] * EMBEDDING_DIM],
            )
            rows = vs.vector_search([1.0] * EMBEDDING_DIM, top_k=5, collection="t")
            assert len(rows) == 1, "回退 FLOAT 后检索应正常返回命中文档"
        finally:
            vs.close()

    def test_int8_serializer_clamps_and_quantizes(self) -> None:
        """int8 序列化：[-1,1] 钳制 + round(x*127) 到 int8 字节。"""
        from doc2mind.core.store.sqlite_vec import _vector_to_bytes_int8

        b = _vector_to_bytes_int8([1.0, -1.0, 0.5, -0.5, 5.0, -9.0])
        assert isinstance(b, bytes) and len(b) == 6
        values = list(b)
        # int8 有符号补码：127=127, -127=129
        assert values[0] == 127
        assert values[1] == 129  # -127
        assert values[2] == 64  # round(0.5*127)=round(63.5)=64
        assert values[3] == 192  # -64 → 128+64
        # 越界被钳制
        assert values[4] == 127
        assert values[5] == 129


class TestD2SparseRetrieval:
    """D2 稀疏向量召回路：稀疏倒排索引 + 三路融合 + 权重解析。"""

    def _sparse_store(self, tmp_path, *, enabled=True) -> VectorStore:
        vs = VectorStore(
            tmp_path / "d2.db", embedding_dim=EMBEDDING_DIM,
            bm25_jieba_enabled=True, sparse_retrieval_enabled=enabled,
        )
        try:
            vs.open()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"sqlite-vec 不可用: {e}")
        for i, content in enumerate(["气缸报警排查电磁阀", "应收账款对账汇总"]):
            vs.upsert_document(StoredDocument(
                id=f"d{i}", source=f"s{i}.md", collection="t", format="md",
                file_hash=f"h{i}", size_bytes=100, page_count=None, chunk_count=1,
                created_at="2026-01-01T00:00:00+08:00",
                updated_at="2026-01-01T00:00:00+08:00",
            ))
            vs.insert_chunks(
                document_id=f"d{i}", collection="t", source=f"s{i}.md", fmt="md",
                chunks=[Chunk(content=content, tokens=len(content),
                              metadata={"chunk_index": 0})],
                embeddings=[_unit_vec(i + 1)],
            )
        return vs

    def test_sparse_search_returns_cosine(self, tmp_path) -> None:
        vs = self._sparse_store(tmp_path)
        try:
            hits = vs.sparse_search("气缸", top_k=5, collection="t")
            assert hits, "稀疏路必须召回共享词文档"
            cids = [cid for cid, _ in hits]
            assert 1 in cids  # s1.md = 气缸报警排查电磁阀
            for cid, score in hits:
                assert 0.0 <= score <= 1.0, "稀疏余弦 ∈ [0,1]，与忠实余弦同量纲"
            # 关闭稀疏 → 空
        finally:
            vs.close()

    def test_sparse_available_flag_toggle(self, tmp_path) -> None:
        on = self._sparse_store(tmp_path, enabled=True)
        off = self._sparse_store(tmp_path / "off.db", enabled=False)
        try:
            assert on.sparse_available is True
            assert off.sparse_available is False
            assert off.sparse_search("气缸", top_k=5, collection="t") == []
        finally:
            on.close()
            off.close()

    def test_three_leg_retriever_populates_sparse_score(self, tmp_path) -> None:
        """开启稀疏路 + 三路权重时，SearchHit.sparse_score 被填充、stats 计入稀疏路。"""
        vs = self._sparse_store(tmp_path)
        try:
            retriever = Retriever(
                store=vs, embedder=_FlatEmbedder(), reranker=None,
                rrf_weights=(1.0, 1.0, 1.0),
            )
            hits, stats = retriever.search("气缸", collection="t", top_k=5)
            assert stats.sparse_enabled is True
            assert stats.sparse_candidates > 0
            assert any(h.sparse_score > 0 for h in hits), "稀疏路分数应被带上"
        finally:
            vs.close()

    def test_sparse_leg_redundant_when_weight_zero(self, tmp_path) -> None:
        """默认两路权重（第三位 0）→ 稀疏路不参与但仍可用（向后兼容）。"""
        vs = self._sparse_store(tmp_path)
        try:
            retriever = Retriever(
                store=vs, embedder=_FlatEmbedder(), reranker=None,
                rrf_weights=(1.0, 1.0),
            )
            hits, stats = retriever.search("气缸", collection="t", top_k=5)
            assert stats.sparse_enabled is False
            assert stats.sparse_candidates == 0
            assert len(hits) >= 1  # 两路仍正常工作
        finally:
            vs.close()


class TestD2MultipathAviB:
    """D2 多路召回 A/B：两路 vs 三路（确定性中文语料）不退化。"""

    def test_three_leg_not_worse_than_two_leg(self) -> None:
        r = eval_retrieval.compare_multipath_zh()
        assert r["gate_passed"] is True, r
        assert r["three_leg"]["Recall@3"] >= r["two_leg"]["Recall@3"] - 1e-9
        # 指标键一致
        assert set(r["two_leg"]) == set(r["three_leg"])

    def test_two_leg_matches_zh_baseline(self) -> None:
        """三路 A/B 的两路结果应与 D1 中文基准一致（同一算法路径）。"""
        mp = eval_retrieval.compare_multipath_zh()
        base = eval_retrieval.evaluate_zh()
        assert mp["two_leg"] == base["metrics"]
        assert mp["engine"] == base["engine"]

    def test_deterministic_stable(self) -> None:
        a = eval_retrieval.compare_multipath_zh()
        b = eval_retrieval.compare_multipath_zh()
        assert a["two_leg"] == b["two_leg"]
        assert a["three_leg"] == b["three_leg"]


class TestD3Autocalibrate:
    """D3 阈值/参数自动标定：权重扫描 + semantic_floor/pitfall 建议 + 重排温度。"""

    def test_non_degradation_gate(self) -> None:
        """选出的最优权重 Recall@3 不得劣于两路基线（召回退化门）。"""
        r = eval_retrieval.autocalibrate_zh()
        assert r["best_rrf_weights"] in eval_retrieval.RRF_WEIGHT_SCAN
        assert r["best_metrics"]["Recall@3"] >= r["baseline"]["Recall@3"] - 1e-9
        assert r["best_metrics"]["MRR"] >= r["baseline"]["MRR"] - 1e-9

    def test_deterministic_stable(self) -> None:
        a = eval_retrieval.autocalibrate_zh()
        b = eval_retrieval.autocalibrate_zh()
        assert a["best_rrf_weights"] == b["best_rrf_weights"]
        assert a["recommended"] == b["recommended"]

    def test_recommended_writable_back_to_config(self) -> None:
        """recommended.rrf_weights 应能被 config.parse_rrf_weights 解析为 3 元。"""
        from doc2mind.core.config import parse_rrf_weights

        r = eval_retrieval.autocalibrate_zh()
        rec = r["recommended"]
        assert set(rec) == {"rrf_weights", "semantic_floor",
                            "pitfall_min_score", "rerank_calibration_temperature"}
        v, b, s = parse_rrf_weights(rec["rrf_weights"])
        assert (v, b, s) == tuple(r["best_rrf_weights"])
        assert isinstance(rec["semantic_floor"], float)
        assert rec["rerank_calibration_temperature"] == 1.0

    def test_semantic_floor_preserves_recall(self) -> None:
        """suggested floor 保守：应用后 Recall@3 损失应在容忍内（回测确认）。"""
        r = eval_retrieval.autocalibrate_zh()
        floor = r["semantic_floor"]
        assert floor["suggested"] >= 0.0
        if floor["suggested"] > 0.0:
            # 扫描表里已含「建议是否保住召回」——直接校验存在于 scan 且达标
            hit = next((s for s in floor["scan"] if abs(s["floor"] - floor["suggested"]) < 1e-9), None)
            if hit is not None:
                tol = floor["tolerance"]
                assert hit["recall_loss"] <= tol + 1e-9

    def test_pitfall_never_above_min_positive(self) -> None:
        """pitfall 建议不得越过最小正样代理分（不挡相关答案）。"""
        # 直接单测纯函数：构造正负代理分样本（顺序 = (is_relevant, proxy)）
        lbl = [(True, 0.72), (True, 0.65), (False, 0.90), (False, 0.80)]
        pit = eval_retrieval._calibrate_pitfall(lbl)
        assert pit["suggested"] <= min(  # 正负重叠 → 保守取 min_pos，不挡正样
            p for rel, p in lbl if rel
        )
        # 可分：取中点，且严格落在 (max_neg, min_pos)
        sep = eval_retrieval._calibrate_pitfall([
            (False, 0.2), (True, 1.0), (True, 0.8),
        ])
        assert sep["suggested"] == 0.5
        assert sep["max_neg"] == 0.2 and sep["min_pos"] == 0.8

    def test_rerank_temperature_optimizer(self) -> None:
        """良好分划的数据应选出低对数损失的温度；空输入保持默认 1.0。"""
        opt = eval_retrieval.autocalibrate_rerank_temperature([
            (5.0, True), (4.0, True), (-5.0, False), (-4.0, False),
        ])
        assert opt["n"] == 4
        assert opt["min_binary_log_loss"] < 0.2  # 分划良好 → 损失低
        assert opt["suggested_temperature"] >= 0.3
        empty = eval_retrieval.autocalibrate_rerank_temperature([])
        assert empty["suggested_temperature"] == 1.0