"""摄入性能改造的回归测试。

覆盖本轮优化的核心行为：
1. 去重前移：重复导入在解析前被跳过，loader.extract 不被调用
2. 文件级并行（ingest_workers>1）：两段式正确性（顺序/计数）
3. auto-curate 后台化：导入路径不再同步调 LLM，文档 id 移交后台
4. 页级进度：loader.progress 折算成 parsing 阶段 stage_progress
5. DB 批量写：executemany 后各表行数正确（含 sparse 开关路径）
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.config import Settings
from doc2mind.core.loader.base import Loader
from doc2mind.core.models import (
    DocFormat,
    DocumentElement,
    ElementType,
    LoadedDocument,
)
from doc2mind.core.pipeline import (
    ingest_path,
    run_background_curate,
)
from doc2mind.core.loader.base import stream_file_hash
from doc2mind.core.store.sqlite_vec import VectorStore

EMBEDDING_DIM = 8


class _SeqEmbedder:
    """确定性嵌入器：每个 chunk 返回固定向量（与既有测试同构）。"""

    dimension = EMBEDDING_DIM

    def embed(self, chunks: list[Chunk]):
        for c in chunks:
            yield [1.0] * EMBEDDING_DIM

    def embed_query(self, text: str) -> list[float]:
        return [1.0] * EMBEDDING_DIM


def _write_md(tmp_path: Path, name: str) -> Path:
    f = tmp_path / name
    f.write_text(
        f"# {name}\n\n第一段内容，用于分块测试。这段文字足够长，可以被切出一个独立的块。\n\n"
        "第二段内容，同样用于分块测试。文档解析后应产出至少一个有效分块。\n",
        encoding="utf-8",
    )
    return f


class _ExtractSpyLoader(Loader):
    """记录 extract 被调用次数的假 loader（用于验证去重前移不触发解析）。"""

    supported_extensions = ("md",)
    calls = 0

    def extract(self, path: Path, progress=None) -> LoadedDocument:
        type(self).calls += 1
        data = path.read_bytes()
        text = path.read_text(encoding="utf-8")
        return LoadedDocument(
            source=str(path),
            format=DocFormat.MARKDOWN,
            elements=[
                DocumentElement(
                    content=text,
                    type=ElementType.PARAGRAPH,
                    metadata={"source_format": DocFormat.MARKDOWN.value},
                )
            ],
            page_count=None,
            size_bytes=len(data),
            file_hash=hashlib.md5(data).hexdigest(),
        )


# --- 1. 去重前移 ---
class TestDedupPrecheck:
    def test_second_ingest_skips_without_parsing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _SeqEmbedder()
        )
        monkeypatch.setattr("doc2mind.core.pipeline.get_loader",
                            lambda path: _ExtractSpyLoader())
        _ExtractSpyLoader.calls = 0

        f = _write_md(tmp_path, "doc.md")
        settings = Settings(db_path=tmp_path / "s.db")
        first = ingest_path(path=f, settings=settings, collection="default")
        assert first.total_documents == 1
        assert _ExtractSpyLoader.calls == 1

        # 再次导入同一文件：应被去重前移命中，跳过解析/分块/嵌入
        second = ingest_path(path=f, settings=settings, collection="default")
        assert second.skipped == 1
        assert second.total_documents == 0
        assert _ExtractSpyLoader.calls == 1, "重复导入不应再次调用 loader.extract"

    def test_streaming_hash_matches_loader_hash(self, tmp_path) -> None:
        f = _write_md(tmp_path, "doc.md")
        data = f.read_bytes()
        import hashlib

        assert stream_file_hash(f) == (hashlib.md5(data).hexdigest(), len(data))

    def test_force_reingests(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _SeqEmbedder()
        )
        monkeypatch.setattr("doc2mind.core.pipeline.get_loader",
                            lambda path: _ExtractSpyLoader())
        _ExtractSpyLoader.calls = 0

        f = _write_md(tmp_path, "doc.md")
        settings = Settings(db_path=tmp_path / "s.db")
        ingest_path(path=f, settings=settings, collection="default")
        ingest_path(path=f, settings=settings, collection="default", force=True)
        assert _ExtractSpyLoader.calls == 2, "force=True 应绕过去重重新解析"


# --- 2. 文件级并行 ---
class TestParallelIngest:
    def test_parallel_workers_order_and_counts(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _SeqEmbedder()
        )
        d = tmp_path / "docs"
        d.mkdir()
        for i in range(6):
            _write_md(d, f"doc{i}.md")

        settings = Settings(db_path=tmp_path / "s.db", ingest_workers=3)
        summary = ingest_path(path=d, settings=settings, collection="default")

        assert summary.total_documents == 6
        assert summary.skipped == 0
        assert summary.failed == 0
        # 结果按提交顺序（排序后文件名序）排列
        assert [r.source.endswith(f"doc{i}.md") for i, r in enumerate(summary.results)] == [
            True
        ] * 6

    def test_parallel_equals_sequential_results(self, tmp_path, monkeypatch) -> None:
        """并行与串行结果等价（同一批文件、同一库内容）。"""
        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _SeqEmbedder()
        )
        d = tmp_path / "docs"
        d.mkdir()
        for i in range(4):
            _write_md(d, f"doc{i}.md")

        seq = ingest_path(
            path=d, settings=Settings(db_path=tmp_path / "seq.db"),
            collection="default",
        )
        par = ingest_path(
            path=d, settings=Settings(db_path=tmp_path / "par.db", ingest_workers=2),
            collection="default",
        )
        assert par.total_documents == seq.total_documents
        assert par.total_chunks == seq.total_chunks
        assert [r.status for r in par.results] == [r.status for r in seq.results]


# --- 3. auto-curate 后台化 ---
class TestCurateDeferred:
    def test_ingest_defers_curate_and_exposes_ids(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _SeqEmbedder()
        )
        recorded: list = []

        def fake_curate(store, settings, document_id, allow_categorize):
            recorded.append((document_id, allow_categorize))

        monkeypatch.setattr(
            "doc2mind.core.pipeline._auto_curate_after_ingest", fake_curate
        )
        d = tmp_path / "docs"
        d.mkdir()
        _write_md(d, "a.md")
        _write_md(d, "b.md")

        settings = Settings(db_path=tmp_path / "s.db", auto_curate_on_ingest=True)
        summary = ingest_path(path=d, settings=settings, collection="default")

        # 导入热路径不再同步调 LLM 整理
        assert recorded == []
        assert len(summary.curatable_document_ids) == 2

        # 后台执行器逐文档调用（allow_categorize=False：文件摄入不自动归类）
        result = run_background_curate(
            settings, summary.curatable_document_ids, "default"
        )
        assert result["processed"] == 2
        assert result["failed"] == 0
        assert [r[1] for r in recorded] == [False, False]
        assert [r[0] for r in recorded] == summary.curatable_document_ids

    def test_background_curate_honors_cancel(self, tmp_path, monkeypatch) -> None:
        recorded: list = []
        monkeypatch.setattr(
            "doc2mind.core.pipeline._auto_curate_after_ingest",
            lambda *a, **k: recorded.append(a),
        )
        settings = Settings(db_path=tmp_path / "s.db", auto_curate_on_ingest=True)
        ev = threading.Event()
        ev.set()
        result = run_background_curate(
            settings, ["doc-1", "doc-2"], "default", cancel_event=ev
        )
        assert result["cancelled"] is True
        assert result["processed"] == 0
        assert recorded == []


# --- 4. 页级进度 ---
class TestPageProgress:
    def test_loader_page_progress_maps_to_parsing(self, tmp_path, monkeypatch) -> None:
        class _PageLoader(Loader):
            supported_extensions = ("md",)

            def extract(self, path: Path, progress=None) -> LoadedDocument:
                if progress is not None:
                    progress(2, 5)
                return LoadedDocument(
                    source=str(path),
                    format=DocFormat.MARKDOWN,
                    elements=[
                        DocumentElement(
                            content="# t\n\n内容足够长以产出分块。",
                            type=ElementType.HEADING,
                            metadata={},
                        )
                    ],
                    page_count=5,
                    size_bytes=12,
                    file_hash="page-hash",
                )

        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _SeqEmbedder()
        )
        monkeypatch.setattr("doc2mind.core.pipeline.get_loader",
                            lambda path: _PageLoader())

        f = _write_md(tmp_path, "doc.md")
        frames: list = []
        ingest_path(
            path=f,
            settings=Settings(db_path=tmp_path / "s.db"),
            collection="default",
            progress=lambda done, total, current_file=None, stage=None,
            stage_progress=None: frames.append((stage, stage_progress)),
        )
        parsing = [f for f in frames if f[0] == "parsing" and f[1] is not None]
        assert parsing, "页级进度应折算成 parsing 阶段 stage_progress"
        assert parsing[-1][1] == pytest.approx(2 / 5)


# --- 5. DB 批量写 ---
class TestBatchedWrites:
    def test_executemany_row_counts_with_sparse(self, tmp_path) -> None:
        settings = Settings(
            db_path=tmp_path / "s.db",
            sparse_retrieval_enabled=True,
            chunk_max_tokens=1500,
        )
        store = VectorStore(
            settings.db_path, EMBEDDING_DIM,
            bm25_jieba_enabled=settings.bm25_jieba_enabled,
            sparse_retrieval_enabled=True,
        )
        store.open()
        try:
            from doc2mind.core.pipeline import _now_iso
            from doc2mind.core.store.sqlite_vec import StoredDocument

            chunks = [
                Chunk(content=f"这是第 {i} 块的分块内容，用于批量写入校验。",
                      metadata={"chunk_index": i, "type": "paragraph"})
                for i in range(5)
            ]
            embeddings = [[float(i)] * EMBEDDING_DIM for i in range(5)]
            doc = StoredDocument(
                id="d1", source="/x/a.md", collection="default",
                format="md", file_hash="h", size_bytes=100,
                page_count=None, chunk_count=5,
                created_at=_now_iso(), updated_at=_now_iso(),
            )
            inserted = store.replace_document(doc, chunks, embeddings)
            assert inserted == 5

            meta_count = store._conn.execute(
                "SELECT COUNT(*) FROM chunks_meta WHERE document_id='d1'"
            ).fetchone()[0]
            fts_count = store._conn.execute(
                "SELECT COUNT(*) FROM bm25_index WHERE CAST(chunk_id AS INTEGER) IN "
                "(SELECT id FROM chunks_meta WHERE document_id='d1')"
            ).fetchone()[0]
            sparse_count = store._conn.execute(
                "SELECT COUNT(*) FROM sparse_terms WHERE chunk_id IN "
                "(SELECT id FROM chunks_meta WHERE document_id='d1')"
            ).fetchone()[0]
            assert meta_count == 5
            assert fts_count == 5
            assert sparse_count > 0

            # 向量表行数与分块一致（三路索引都可查询）
            vec_count = store._conn.execute(
                "SELECT COUNT(*) FROM vec_chunks"
            ).fetchone()[0]
            assert vec_count == 5
        finally:
            store.close()

    def test_replace_document_clears_old_then_writes(self, tmp_path) -> None:
        """替换语义保持：同 source 重导先清旧行再写新行，无孤儿残留。"""
        settings = Settings(db_path=tmp_path / "s.db")
        store = VectorStore(settings.db_path, EMBEDDING_DIM,
                            bm25_jieba_enabled=settings.bm25_jieba_enabled,
                            sparse_retrieval_enabled=False)
        store.open()
        try:
            from doc2mind.core.pipeline import _now_iso
            from doc2mind.core.store.sqlite_vec import StoredDocument

            def _doc(doc_id: str, chunks: int, base: int):
                return StoredDocument(
                    id=doc_id, source="/x/a.md", collection="default",
                    format="md", file_hash=f"h{base}", size_bytes=100,
                    page_count=None, chunk_count=chunks,
                    created_at=_now_iso(), updated_at=_now_iso(),
                )

            ch1 = [Chunk(content=f"第一版内容 {i}", metadata={"chunk_index": i})
                   for i in range(3)]
            ch2 = [Chunk(content=f"第二版内容 {i}", metadata={"chunk_index": i})
                   for i in range(4)]
            store.replace_document(_doc("old", 3, 1), ch1,
                                   [[0.0] * EMBEDDING_DIM for _ in range(3)])
            store.replace_document(_doc("new", 4, 2), ch2,
                                   [[0.0] * EMBEDDING_DIM for _ in range(4)])

            rows = store._conn.execute(
                "SELECT COUNT(*) FROM chunks_meta WHERE document_id='old'"
            ).fetchone()[0]
            assert rows == 0, "替换后旧文档分块应被清空"
            rows = store._conn.execute(
                "SELECT COUNT(*) FROM chunks_meta WHERE document_id='new'"
            ).fetchone()[0]
            assert rows == 4
        finally:
            store.close()
