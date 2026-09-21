"""文件内阶段进度：ingest_path 阶段上报、stage_fraction 折算、http job 进度加权。

背景：单大文件导入时 done 长时间为 0，进度条停在 done/total 上像卡死。
进度回调因此扩展了 (stage, stage_progress)，嵌入阶段按批次上报真实进度，
消费方用固定跨度折算成文件内整体进度。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.pipeline import (
    STAGE_SPANS,
    _make_stage_reporter,
    ingest_path,
    stage_fraction,
)
from doc2mind.core.config import Settings

EMBEDDING_DIM = 8


class _SeqEmbedder:
    """确定性嵌入器：每个 chunk 返回固定向量，惰性逐个 yield（模拟 fastembed）。"""

    dimension = EMBEDDING_DIM

    def embed(self, chunks: list[Chunk]):
        for c in chunks:
            yield [1.0] * EMBEDDING_DIM

    def embed_query(self, text: str) -> list[float]:
        return [1.0] * EMBEDDING_DIM


class _BatchListEmbedder(_SeqEmbedder):
    """按批次 yield 列表（防御未来嵌入器按批返回的情况）。"""

    def embed(self, chunks: list[Chunk]):
        batch: list[list[float]] = []
        for c in chunks:
            batch.append([1.0] * EMBEDDING_DIM)
            if len(batch) >= 2:
                yield batch
                batch = []
        if batch:
            yield batch


def _write_md(tmp_path: Path, name: str) -> Path:
    f = tmp_path / name
    f.write_text(
        f"# {name}\n\n第一段内容，用于分块测试。这段文字足够长，可以被切出一个独立的块。\n\n"
        "第二段内容，同样用于分块测试。文档解析后应产出至少一个有效分块。\n",
        encoding="utf-8",
    )
    return f


# --- stage_fraction 折算 ---
class TestStageFraction:
    def test_none_stage_is_zero(self) -> None:
        assert stage_fraction(None, None) == 0.0
        assert stage_fraction(None, 0.7) == 0.0

    def test_stage_without_progress_uses_span_start(self) -> None:
        assert stage_fraction("parsing", None) == 0.0
        assert stage_fraction("chunking", None) == pytest.approx(0.5)
        assert stage_fraction("writing", None) == pytest.approx(0.9)

    def test_embedding_progress_interpolates(self) -> None:
        start, end = STAGE_SPANS["embedding"]
        assert stage_fraction("embedding", 0.0) == pytest.approx(start)
        assert stage_fraction("embedding", 1.0) == pytest.approx(end)
        assert stage_fraction("embedding", 0.5) == pytest.approx((start + end) / 2)

    def test_unknown_stage_and_clamping(self) -> None:
        assert stage_fraction("unknown", 0.5) == 0.0
        assert stage_fraction("embedding", 1.5) == pytest.approx(STAGE_SPANS["embedding"][1])
        assert stage_fraction("embedding", -0.1) == pytest.approx(STAGE_SPANS["embedding"][0])

    def test_spans_are_monotonic(self) -> None:
        order = ["parsing", "chunking", "embedding", "writing", "curating"]
        spans = [STAGE_SPANS[s] for s in order]
        for (_, end_a), (start_b, _) in zip(spans, spans[1:], strict=False):
            assert end_a <= start_b
        assert spans[0][0] == 0.0
        assert spans[-1][1] == 1.0


# --- ingest_path 阶段上报 ---
class TestIngestPathStageProgress:
    def test_reports_total_immediately_then_stages(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _SeqEmbedder()
        )
        f = _write_md(tmp_path, "doc.md")
        calls: list = []
        summary = ingest_path(
            path=f,
            settings=Settings(db_path=tmp_path / "s1.db"),
            collection="default",
            progress=lambda done, total, current_file=None, stage=None, stage_progress=None:
                calls.append((done, total, current_file, stage, stage_progress)),
        )
        assert summary.total_documents == 1

        # 首帧必须是 (0, total)：前端立刻拿到文件总数，而不是等第一个文件完成
        assert calls[0] == (0, 1, None, None, None)

        stages = [c for c in calls if c[3] is not None]
        stage_names = [c[3] for c in stages]
        # 阶段按序出现且每个阶段至少上报一次；嵌入阶段进度从 0 推进到 1
        assert stage_names[0] == "parsing"
        assert "chunking" in stage_names
        assert "embedding" in stage_names
        assert "writing" in stage_names
        embedding = [c for c in stages if c[3] == "embedding"]
        assert embedding[0][4] == 0.0
        assert embedding[-1][4] == pytest.approx(1.0)
        assert all(a[4] <= b[4] for a, b in zip(embedding, embedding[1:], strict=False))
        # 阶段帧携带当前文件名，done 偏移为"已完成的文件数"
        assert all(c[2] == "doc.md" for c in stages)
        assert all(c[0] == 0 for c in stages)

        # 最后一帧：文件完成（done=1，阶段清空）
        assert calls[-1][:2] == (1, 1)
        assert calls[-1][3] is None

    def test_batch_list_embedder_also_reports(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _BatchListEmbedder()
        )
        f = _write_md(tmp_path, "doc.md")
        summary = ingest_path(
            path=f,
            settings=Settings(db_path=tmp_path / "s2.db"),
            collection="default",
        )
        assert summary.total_documents == 1

    def test_legacy_two_arg_callback_still_works(self, tmp_path, monkeypatch) -> None:
        """只接受 (done, total) 的旧回调不应被阶段上报破坏。"""
        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _SeqEmbedder()
        )
        f = _write_md(tmp_path, "doc.md")
        calls: list = []

        def legacy(done: int, total: int) -> None:
            calls.append((done, total))

        summary = ingest_path(
            path=f,
            settings=Settings(db_path=tmp_path / "s3.db"),
            collection="default",
            progress=legacy,
        )
        assert summary.total_documents == 1
        assert calls[0] == (0, 1)
        assert calls[-1] == (1, 1)

    def test_multi_file_done_offset_advances(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda settings: _SeqEmbedder()
        )
        d = tmp_path / "docs"
        d.mkdir()
        _write_md(d, "a.md")
        _write_md(d, "b.md")
        stages: list = []
        summary = ingest_path(
            path=d,
            settings=Settings(db_path=tmp_path / "s4.db"),
            collection="default",
            progress=lambda done, total, current_file=None, stage=None, stage_progress=None:
                stages.append((done, current_file, stage) if stage else (done, total)),
        )
        assert summary.total_documents == 2
        # 第二个文件的阶段帧 done 偏移应为 1（第一个文件已完成）
        second_file_stages = [c for c in stages if len(c) == 3 and c[0] == 1]
        assert second_file_stages, "第二个文件处理期间应有 done=1 的阶段帧"
        assert all(c[1] in ("a.md", "b.md") for c in second_file_stages)


# --- _make_stage_reporter 兼容降级 ---
class TestMakeStageReporter:
    def test_full_signature_callback_receives_stage(self) -> None:
        seen: list = []

        def cb(done, total, current_file=None, stage=None, stage_progress=None) -> None:
            seen.append((done, total, current_file, stage, stage_progress))

        reporter = _make_stage_reporter(cb, done=1, total=3, current_file="x.pdf")
        reporter("embedding", 0.5)
        assert seen == [(1, 3, "x.pdf", "embedding", 0.5)]

    def test_two_arg_callback_falls_back(self) -> None:
        seen: list = []

        def cb(done, total) -> None:
            seen.append((done, total))

        reporter = _make_stage_reporter(cb, done=1, total=3, current_file="x.pdf")
        reporter("embedding", 0.5)
        assert seen == [(1, 3)]


# --- http._update_ingest_job 加权进度 ---
class TestUpdateIngestJobWeightedProgress:
    def _make_state(self):
        from doc2mind.server.http import _AppState

        return _AppState()

    def test_weighted_progress_with_stage(self, monkeypatch) -> None:
        import doc2mind.server.http as http_mod

        broadcasts: list = []
        monkeypatch.setattr(
            http_mod, "_broadcast_job_event",
            lambda job_id, payload: broadcasts.append(payload),
        )
        from doc2mind.server.http import JobStatus

        job = JobStatus(
            job_id="j1", type="ingest", status="running",
            started_at="2026-01-01T00:00:00Z",
        )
        state = self._make_state()
        # 单文件、嵌入过半：整体 = (0 + 0.6 + 0.3*0.5) / 1 = 0.75
        http_mod._update_ingest_job(
            state, job, done=0, total=1,
            current_file="big.pdf", stage="embedding", stage_progress=0.5,
        )
        assert job.progress == pytest.approx(0.75)
        assert job.stage == "embedding"
        assert job.stage_progress == pytest.approx(0.5)
        assert job.current_file == "big.pdf"
        assert broadcasts, "应广播 SSE 事件"
        assert broadcasts[-1]["stage"] == "embedding"
        assert broadcasts[-1]["stage_progress"] == pytest.approx(0.5)

    def test_file_completion_clears_stage(self, monkeypatch) -> None:
        import doc2mind.server.http as http_mod

        monkeypatch.setattr(http_mod, "_broadcast_job_event", lambda *a, **k: None)
        from doc2mind.server.http import JobStatus

        job = JobStatus(
            job_id="j2", type="ingest", status="running",
            started_at="2026-01-01T00:00:00Z",
        )
        state = self._make_state()
        http_mod._update_ingest_job(
            state, job, done=0, total=1, stage="embedding", stage_progress=1.0,
        )
        http_mod._update_ingest_job(state, job, done=1, total=1)
        # 文件完成后阶段清空，整体进度 = 1/1，且不倒退
        assert job.stage is None
        assert job.progress == pytest.approx(1.0)

    def test_total_zero_stays_zero(self, monkeypatch) -> None:
        import doc2mind.server.http as http_mod

        monkeypatch.setattr(http_mod, "_broadcast_job_event", lambda *a, **k: None)
        from doc2mind.server.http import JobStatus

        job = JobStatus(
            job_id="j3", type="ingest", status="running",
            started_at="2026-01-01T00:00:00Z",
        )
        state = self._make_state()
        http_mod._update_ingest_job(state, job, done=0, total=0)
        assert job.progress == 0.0


# --- JobStatus 序列化带 stage 字段 ---
class TestJobStatusStageFields:
    def test_fields_serialize(self) -> None:
        from doc2mind.server.http import JobStatus

        job = JobStatus(
            job_id="x", type="ingest", status="running",
            started_at="2026-01-01T00:00:00Z",
            stage="embedding", stage_progress=0.42,
        )
        dumped = job.model_dump()
        assert dumped["stage"] == "embedding"
        assert dumped["stage_progress"] == pytest.approx(0.42)

        job2 = JobStatus(
            job_id="y", type="ingest", status="running",
            started_at="2026-01-01T00:00:00Z",
        )
        assert job2.model_dump()["stage"] is None
        assert job2.model_dump()["stage_progress"] is None
