"""http.py 新增端点单测：dependencies / install-ocr / download-model / jobs-events。

用 TestClient 验证路由可达、返回字段契约、SSE 帧格式。
不实际执行 pip 安装或模型下载（mock 掉底层）。
"""

from __future__ import annotations

import json
from unittest.mock import patch


def _make_client():
    from fastapi.testclient import TestClient

    from doc2mind.server.http import create_app

    app = create_app()
    return TestClient(app)


# --- GET /v1/system/dependencies ---
class TestDependenciesEndpoint:
    def test_returns_status_fields(self) -> None:
        with _make_client() as client:
            resp = client.get("/v1/system/dependencies")
        assert resp.status_code == 200
        data = resp.json()
        required = {
            "gpu_available",
            "ocr_available",
            "model_cached",
            "poppler_available",
            "recommended_path",
            "installed_packages",
            "warnings",
        }
        assert required.issubset(data.keys()), f"缺失: {required - data.keys()}"
        assert isinstance(data["gpu_available"], bool)
        assert isinstance(data["ocr_available"], bool)


# --- POST /v1/system/install-ocr (SSE) ---
class TestInstallOcrEndpoint:
    def test_sse_frame_format(self) -> None:
        """mock install_ocr_packages 产出固定事件，验证 SSE 帧格式。"""

        async def _fake_install(path: str):
            yield {"type": "log", "line": "fake log"}
            yield {"type": "done", "success": True, "path": path}

        with patch("doc2mind.core.system_env.install_ocr_packages", _fake_install):
            with _make_client() as client:
                resp = client.post("/v1/system/install-ocr", json={"path": "cpu"})
        assert resp.status_code == 200
        body = resp.text
        # 应包含 data: 前缀帧和 [DONE]
        assert "data: " in body
        assert "[DONE]" in body
        # 解析首帧
        lines = [l for l in body.splitlines() if l.startswith("data: ") and l[6:] != "[DONE]"]
        assert len(lines) >= 2
        first = json.loads(lines[0][6:])
        assert first["type"] == "log"
        assert first["line"] == "fake log"

    def test_unknown_path_yields_error(self) -> None:
        with _make_client() as client:
            resp = client.post("/v1/system/install-ocr", json={"path": "bad-path"})
        assert resp.status_code == 200  # SSE 总是 200，错误在帧里
        assert "error" in resp.text


# --- GET /v1/jobs/{id}/events ---
class TestJobEventsEndpoint:
    def test_not_found_job(self) -> None:
        with _make_client() as client:
            resp = client.get("/v1/jobs/nonexistent-id/events")
        assert resp.status_code == 404

    def test_terminal_job_streams_snapshot_then_done(self) -> None:
        """已完成的 job 应立即推快照 + done + [DONE] 后关闭。"""
        from doc2mind.server.http import JobStatus

        with _make_client() as client:
            # 手动注入一个已完成 job
            state = client.app.state.doc2mind
            job = JobStatus(
                job_id="test-done",
                type="ingest",
                status="completed",
                progress=1.0,
                processed=5,
                total=5,
                started_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:01:00Z",
            )
            with state._jobs_lock:
                state.jobs["test-done"] = job

            resp = client.get("/v1/jobs/test-done/events")
        assert resp.status_code == 200
        body = resp.text
        assert "data: " in body
        assert "[DONE]" in body
        # 首帧应是 progress 快照
        lines = [l for l in body.splitlines() if l.startswith("data: ") and l[6:] != "[DONE]"]
        first = json.loads(lines[0][6:])
        assert first["type"] == "progress"
        assert first["status"] == "completed"


# --- JobStatus.current_file 字段 ---
class TestJobStatusCurrentFile:
    def test_field_exists_and_serializes(self) -> None:
        from doc2mind.server.http import JobStatus

        job = JobStatus(
            job_id="x",
            type="ingest",
            status="running",
            started_at="2026-01-01T00:00:00Z",
            current_file="report.pdf",
        )
        dumped = job.model_dump()
        assert dumped["current_file"] == "report.pdf"

        job2 = JobStatus(
            job_id="y",
            type="ingest",
            status="running",
            started_at="2026-01-01T00:00:00Z",
        )
        assert job2.model_dump()["current_file"] is None
