"""整改回归测试 — 2026-08 业务完整性/鲁棒性审查修复。

覆盖：
- P0-2 同名文件 source 含路径（不同目录同名文件不再互相覆盖）
- P0-3 replace_document 单事务原子替换（写失败不留孤儿文档）
- P0-4 维度不匹配预检（导入前给出可操作指引，而不是"写库失败"）
- P0-5 max_tokens sanitize 推广到 Ollama/Anthropic/Gemini
- P0-6 config.toml 损坏/写失败的显式告警
- P1-7  setup_logging 落盘 + 幂等
- P1-8  store.ping / /v1/health 真实探测
- P1-10 min_score 越界忽略 + 降级原因
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.config import Settings
from doc2mind.core.loader.base import make_source
from doc2mind.core.loader.detect import get_loader
from doc2mind.core.pipeline import _ingest_one
from doc2mind.core.store.sqlite_vec import StoredDocument, StoreError, VectorStore

EMBEDDING_DIM = 8


# --- 测试基建：假 embedder + 临时真实库 ---
class FakeEmbedder:
    """确定性假嵌入器：同内容同向量，不加载模型。"""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dimension = dim

    @staticmethod
    def _vec(text: str, dim: int) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        return rng.random(dim, dtype=np.float32)

    def embed(self, chunks):
        for c in chunks:
            yield self._vec(c.content, self.dimension)

    def embed_texts(self, texts):
        return [self._vec(t, self.dimension) for t in texts]

    def embed_query(self, query: str) -> np.ndarray:
        return self._vec(query, self.dimension)


class BrokenEmbedder(FakeEmbedder):
    """embed_query 抛 EmbedderError，模拟嵌入服务不可用。"""

    def embed_query(self, query: str) -> np.ndarray:
        from doc2mind.core.embedder.base import EmbedderError

        raise EmbedderError("模型未加载")


def _open_store(tmp_path: Path) -> VectorStore:
    vs = VectorStore(tmp_path / "test.db", embedding_dim=EMBEDDING_DIM)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001 — sqlite-vec 缺失则跳过
        pytest.skip(f"sqlite-vec 不可用: {e}")
    return vs


def _chunk(content: str) -> Chunk:
    return Chunk(content=content, tokens=len(content), metadata={"chunk_index": 0})


def _doc(doc_id: str, collection: str, source: str, chunks: int) -> StoredDocument:
    return StoredDocument(
        id=doc_id,
        source=source,
        collection=collection,
        format="md",
        file_hash=f"hash-{doc_id}",
        size_bytes=100,
        page_count=None,
        chunk_count=chunks,
        created_at="2026-01-01T00:00:00+08:00",
        updated_at="2026-01-01T00:00:00+08:00",
    )


def _emb(seed_text: str, dim: int = EMBEDDING_DIM) -> np.ndarray:
    return FakeEmbedder._vec(seed_text, dim)


# ----------------------------------------------------------------------
# P0-2 同名文件不互相覆盖
# ----------------------------------------------------------------------
class TestSameNameSource:
    def test_make_source_absolute(self, tmp_path: Path) -> None:
        f = tmp_path / "readme.md"
        f.write_text("# x", encoding="utf-8")
        s = make_source(f)
        assert Path(s).is_absolute()
        assert s.endswith("readme.md")

    def test_loaders_produce_distinct_sources(self, tmp_path: Path) -> None:
        """不同目录同名文件 → source 不同（修复前同为文件名，互相覆盖）。"""
        dir_a, dir_b = tmp_path / "A", tmp_path / "B"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "readme.md").write_text("# 项目甲说明", encoding="utf-8")
        (dir_b / "readme.md").write_text("# 项目乙说明", encoding="utf-8")

        da = get_loader(dir_a / "readme.md").extract(dir_a / "readme.md")
        db = get_loader(dir_b / "readme.md").extract(dir_b / "readme.md")
        assert da.source != db.source

    def test_ingest_same_name_files_coexist(self, tmp_path: Path) -> None:
        """端到端：两个目录同名 md 导入后两份文档都在（修复前只剩一份）。"""
        store = _open_store(tmp_path)
        try:
            dir_a, dir_b = tmp_path / "A", tmp_path / "B"
            dir_a.mkdir()
            dir_b.mkdir()
            (dir_a / "readme.md").write_text("# 甲\nalpha content here", encoding="utf-8")
            (dir_b / "readme.md").write_text("# 乙\nbeta content here", encoding="utf-8")
            settings = Settings()
            emb = FakeEmbedder()

            r1 = _ingest_one(dir_a / "readme.md", settings, "default", False, store, emb)
            r2 = _ingest_one(dir_b / "readme.md", settings, "default", False, store, emb)
            assert r1.status == "ingested"
            assert r2.status == "ingested"

            docs = store.list_documents(collection="default", limit=10)
            assert len(docs) == 2, "不同目录的同名文件必须共存，不能互相覆盖"
        finally:
            store.close()

    def test_reingest_modified_file_replaces_itself(
        self, tmp_path: Path
    ) -> None:
        """同一路径文件修改后重导入：只替换自己，不产生重复文档。"""
        store = _open_store(tmp_path)
        try:
            f = tmp_path / "note.md"
            f.write_text(
                "# v1\noriginal content paragraph with enough length to survive chunking.",
                encoding="utf-8",
            )
            settings = Settings()
            emb = FakeEmbedder()

            r1 = _ingest_one(f, settings, "default", False, store, emb)
            assert r1.status == "ingested"

            f.write_text(
                "# v2\nmodified content paragraph with enough length to survive chunking.",
                encoding="utf-8",
            )
            r2 = _ingest_one(f, settings, "default", False, store, emb)
            assert r2.status == "ingested"

            docs = store.list_documents(collection="default", limit=10)
            assert len(docs) == 1, "同一路径重导入应替换而非新增"
            assert docs[0].id == r2.document_id
            assert docs[0].chunk_count >= 1
            chunks = store.list_chunks_by_document(r2.document_id or "")
            assert any("modified" in c.content for c in chunks)
        finally:
            store.close()


# ----------------------------------------------------------------------
# P0-3 replace_document 原子性
# ----------------------------------------------------------------------
class TestReplaceDocumentAtomic:
    def test_replace_success(self, tmp_path: Path) -> None:
        store = _open_store(tmp_path)
        try:
            old = _doc("d1", "default", str(tmp_path / "a.md"), 2)
            store.replace_document(
                old,
                [_chunk("old one"), _chunk("old two")],
                [_emb("old one"), _emb("old two")],
            )
            new = _doc("d2", "default", str(tmp_path / "a.md"), 1)
            inserted = store.replace_document(
                new, [_chunk("brand new")], [_emb("brand new")]
            )
            assert inserted == 1
            docs = store.list_documents(collection="default", limit=10)
            assert len(docs) == 1
            assert docs[0].id == "d2"
            chunks = store.list_chunks_by_document("d2")
            assert len(chunks) == 1
            assert chunks[0].content == "brand new"
        finally:
            store.close()

    def test_replace_failure_keeps_old_document(self, tmp_path: Path) -> None:
        """写分块失败（维度不匹配）→ 整体回滚，旧文档原样保留。

        修复前三步各自提交：旧文档已被删、新文档记录已写入、分块为空，
        留下"chunk_count>0 但无分块"的孤儿状态。
        """
        store = _open_store(tmp_path)
        try:
            old = _doc("d1", "default", str(tmp_path / "a.md"), 2)
            store.replace_document(
                old,
                [_chunk("keep one"), _chunk("keep two")],
                [_emb("keep one"), _emb("keep two")],
            )

            bad = _doc("d2", "default", str(tmp_path / "a.md"), 1)
            wrong_dim_vec = FakeEmbedder._vec("bad", EMBEDDING_DIM // 2)
            with pytest.raises(StoreError):
                store.replace_document(bad, [_chunk("bad")], [wrong_dim_vec])

            # 旧文档完好：记录、chunk_count、分块内容都在
            docs = store.list_documents(collection="default", limit=10)
            assert len(docs) == 1
            assert docs[0].id == "d1"
            assert docs[0].chunk_count == 2
            chunks = store.list_chunks_by_document("d1")
            assert {c.content for c in chunks} == {"keep one", "keep two"}
        finally:
            store.close()


# ----------------------------------------------------------------------
# P0-4 维度预检
# ----------------------------------------------------------------------
class TestDimensionPrecheck:
    def test_ingest_dimension_mismatch_clear_error(self, tmp_path: Path) -> None:
        """embedder 维度 ≠ 库维度 → 失败信息含指引，且库内无残留。"""
        store = _open_store(tmp_path)
        try:
            f = tmp_path / "note.md"
            f.write_text("# 内容\nsome content to embed", encoding="utf-8")
            mismatched = FakeEmbedder(dim=EMBEDDING_DIM // 2)

            r = _ingest_one(f, Settings(), "default", False, store, mismatched)
            assert r.status == "failed"
            assert "维度" in (r.error or "")
            assert "重建索引" in (r.error or "")
            assert store.count_documents(None, None) == 0
        finally:
            store.close()


# ----------------------------------------------------------------------
# P0-5 max_tokens sanitize 推广
# ----------------------------------------------------------------------
class TestMaxTokensSanitize:
    def test_sanitize_rules(self) -> None:
        from doc2mind.core.llm.base import sanitize_max_tokens

        assert sanitize_max_tokens(None) is None
        assert sanitize_max_tokens(0) == 1
        assert sanitize_max_tokens(-5) == 1
        assert sanitize_max_tokens(2048) == 2048
        assert sanitize_max_tokens(65536) == 65536
        assert sanitize_max_tokens(65537) is None
        assert sanitize_max_tokens(256000) is None  # 误把上下文窗口当输出上限

    def test_openai_alias_compatible(self) -> None:
        from doc2mind.core.llm.base import sanitize_max_tokens
        from doc2mind.core.llm.openai_impl import _sanitize_max_tokens

        assert _sanitize_max_tokens is sanitize_max_tokens

    def test_anthropic_oversize_falls_back(self) -> None:
        from doc2mind.core.llm.anthropic_impl import AnthropicClient

        client = AnthropicClient(api_key="k")
        payload = client._payload(
            [{"role": "user", "content": "hi"}], None, 256000, stream=False
        )
        # Anthropic max_tokens 必填：超上限退回安全默认，而不是透传 256000
        assert payload["max_tokens"] == 8192
        ok = client._payload(
            [{"role": "user", "content": "hi"}], None, 1024, stream=False
        )
        assert ok["max_tokens"] == 1024

    def test_gemini_oversize_omitted(self) -> None:
        from doc2mind.core.llm.gemini_impl import GeminiClient

        client = GeminiClient(api_key="k")
        payload = client._payload([{"role": "user", "content": "hi"}], None, 256000)
        assert "maxOutputTokens" not in payload["generationConfig"]
        ok = client._payload([{"role": "user", "content": "hi"}], None, 512)
        assert ok["generationConfig"]["maxOutputTokens"] == 512

    def test_ollama_oversize_omitted(self) -> None:
        from doc2mind.core.llm.ollama_impl import _options

        assert "num_predict" not in _options(0.7, 256000)
        assert _options(0.7, 512)["num_predict"] == 512


# ----------------------------------------------------------------------
# P0-6 配置读写告警
# ----------------------------------------------------------------------
class TestConfigWarnings:
    def test_corrupt_config_reports_error(self, monkeypatch, tmp_path) -> None:
        import doc2mind.core.config as cfg

        bad = tmp_path / "config.toml"
        bad.write_text("[doc2mind\nbroken =", encoding="utf-8")  # 语法错误
        monkeypatch.setattr(cfg, "config_file_path", lambda: bad)
        cfg._config_load_error = None

        data = cfg.load_config_file()
        assert data == {}
        err = cfg.get_config_load_error()
        assert err and "解析失败" in err

    def test_valid_config_clears_error(self, monkeypatch, tmp_path) -> None:
        import doc2mind.core.config as cfg

        good = tmp_path / "config.toml"
        good.write_text('[doc2mind]\nembed_model = "m"\n', encoding="utf-8")
        monkeypatch.setattr(cfg, "config_file_path", lambda: good)
        cfg._config_load_error = "旧错误"

        data = cfg.load_config_file()
        assert data.get("embed_model") == "m"
        assert cfg.get_config_load_error() is None

    def test_save_settings_failure_returns_false(self, monkeypatch, tmp_path) -> None:
        import doc2mind.core.config as cfg

        blocker = tmp_path / "blocker"
        blocker.write_text("我是一个文件，不能当目录", encoding="utf-8")
        target = blocker / "sub" / "config.toml"  # parent.mkdir 必然失败
        monkeypatch.setattr(cfg, "config_file_path", lambda: target)

        assert cfg.save_settings(Settings()) is False


# ----------------------------------------------------------------------
# P1-7 日志落盘
# ----------------------------------------------------------------------
class TestSetupLogging:
    def test_writes_to_file_and_idempotent(self, monkeypatch, tmp_path) -> None:
        import doc2mind.core.logging_setup as ls

        monkeypatch.setattr(ls, "_user_data_dir", lambda: tmp_path)
        path = ls.setup_logging(force=True)
        assert path == tmp_path / "logs" / "doc2mind.log"

        logging.getLogger("doc2mind.regression").warning("hello-log-file")
        for h in logging.getLogger("doc2mind").handlers:
            h.flush()
        assert path.exists()
        assert "hello-log-file" in path.read_text(encoding="utf-8")

        # 幂等：重复调用不再叠加 handler
        n_handlers = len(logging.getLogger("doc2mind").handlers)
        ls.setup_logging()
        assert len(logging.getLogger("doc2mind").handlers) == n_handlers


# ----------------------------------------------------------------------
# P1-8 健康探测
# ----------------------------------------------------------------------
class TestHealthProbe:
    def test_ping_healthy_store(self, tmp_path: Path) -> None:
        store = _open_store(tmp_path)
        try:
            assert store.ping() is True
        finally:
            store.close()

    def test_ping_closed_store(self, tmp_path: Path) -> None:
        store = _open_store(tmp_path)
        store.close()
        assert store.ping() is False

    def test_health_endpoint_reports_store_ok(
        self, monkeypatch, tmp_path
    ) -> None:
        pytest.importorskip("sqlite_vec")
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/testclient not installed")

        from doc2mind.core.config import set_settings
        from doc2mind.server.http import create_app

        set_settings(Settings(db_path=tmp_path / "health.db"))
        try:
            tc = TestClient(create_app())
            resp = tc.get("/v1/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["store_ok"] is True
            assert data["status"] == "ok"
            assert data["store_error"] is None
        finally:
            from doc2mind.core.config import set_settings as _reset

            _reset(Settings())


# ----------------------------------------------------------------------
# P1-10 检索体验
# ----------------------------------------------------------------------
class TestSearchUX:
    def _store_with_doc(self, tmp_path: Path) -> VectorStore:
        store = _open_store(tmp_path)
        doc = _doc("d1", "default", str(tmp_path / "doc.md"), 1)
        store.replace_document(
            doc, [_chunk("alpha electron search target")], [_emb("alpha electron")]
        )
        return store

    def test_min_score_out_of_rrf_range_ignored(self, tmp_path: Path) -> None:
        """min_score=0.5（超 RRF 上限 ~0.033）→ 忽略过滤并提示，而非清空结果。"""
        from doc2mind.core.retriever.search import Retriever

        store = self._store_with_doc(tmp_path)
        try:
            retriever = Retriever(store=store, embedder=FakeEmbedder())
            hits, stats = retriever.search(
                "alpha electron", collection="default", top_k=5, min_score=0.5
            )
            assert len(hits) > 0, "越界 min_score 应被忽略，结果不应被清空"
            assert stats.message and "min_score" in stats.message
        finally:
            store.close()

    def test_degraded_reports_reason(self, tmp_path: Path) -> None:
        from doc2mind.core.retriever.search import Retriever

        store = self._store_with_doc(tmp_path)
        try:
            retriever = Retriever(store=store, embedder=BrokenEmbedder())
            hits, stats = retriever.search(
                "alpha electron", collection="default", top_k=5
            )
            assert stats.degraded is True
            assert stats.degraded_reason and "嵌入服务不可用" in stats.degraded_reason
        finally:
            store.close()

    def test_search_empty_collection_message(self, tmp_path: Path) -> None:
        """空结果时 message 区分"集合无文档"（端点层逻辑，这里验证 count 基础）。"""
        store = self._store_with_doc(tmp_path)
        try:
            assert store.count_documents(None, None) == 1
            assert store.count_documents("不存在的集合", None) == 0
        finally:
            store.close()
