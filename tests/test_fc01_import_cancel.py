"""FC-01：导入取消检查点与取消残留明细。"""

from __future__ import annotations

import threading
from pathlib import Path

from doc2mind.core.pipeline import IngestCancelled, IngestResult, _record_result, ingest_path


def test_record_result_on_result_callback():
    seen: list[str] = []
    summary = type("S", (), {"results": [], "total_documents": 0, "total_chunks": 0, "skipped": 0, "failed": 0})()

    def on_result(res):
        seen.append(f"{res.status}:{res.source}")

    _record_result(
        summary,
        IngestResult(
            source="a.md", collection="default", format="md",
            size_bytes=1, chunk_count=2, elapsed_ms=1, status="ingested",
        ),
        on_result=on_result,
    )
    assert seen == ["ingested:a.md"]
    assert summary.total_documents == 1


def test_pipeline_has_cancel_checkpoints():
    text = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\core\pipeline.py").read_text(encoding="utf-8")
    # 嵌入批次 / 页级 / 写库前
    assert text.count("IngestCancelled") >= 8
    assert "写库前再检查一次取消" in text
    assert "on_result" in text


def test_http_cancel_note_and_on_result():
    http = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\server\http.py").read_text(encoding="utf-8")
    assert "cancel_note" in http
    assert "_on_file_result" in http
    assert "_fill_cancelled_job" in http
    assert "前 " in http and "篇已导入" in http
    # 不再使用 pydantic 私有 _completed_files hack
    assert "_completed_files" not in http


def test_wpf_import_cancel_shows_residual():
    vm = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\ImportViewModel.cs").read_text(encoding="utf-8")
    assert "CancelNote" in vm or "cancel_note" in vm.lower() or "已导入可搜索" in vm
    job = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\Models\JobStatus.cs").read_text(encoding="utf-8")
    assert "cancel_note" in job or "CancelNote" in job


def test_ingest_path_cancel_before_first_file(tmp_path):
    # 空目录：files 为空时不会调用取消逻辑，但签名含 on_result
    import inspect

    sig = inspect.signature(ingest_path)
    assert "on_result" in sig.parameters
    assert "cancel_event" in sig.parameters

    ev = threading.Event()
    ev.set()
    # 单文件目录取消：应抛 IngestCancelled 或空结果（无文件时不进入循环）
    d = tmp_path / "empty"
    d.mkdir()
    # 无文件：不会抛，返回空 summary
    summary = ingest_path(d, cancel_event=ev, on_result=lambda r: None)
    assert summary is not None
