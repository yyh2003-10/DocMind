"""摄入流水线集成测试。

注意：依赖 sqlite-vec 扩展的测试标记为 `slow`，
运行 `pytest -m "not slow"` 可跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2mind.core.config import Settings
from doc2mind.core.pipeline import ingest_path


class TestIngestPath:
    def test_file_not_found(self) -> None:
        """不存在的文件应直接返回失败结果（无需 sqlite-vec）。"""
        settings = Settings()
        summary = ingest_path(Path("/nonexistent/file.pdf"), settings, "test")
        assert len(summary.results) == 1
        assert summary.results[0].status == "failed"
        assert summary.results[0].error != ""

    @pytest.mark.slow
    def test_unsupported_format(self) -> None:
        """不支持的格式应返回失败结果（需要 sqlite-vec）。"""
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False, mode="w") as f:
            f.write("test")
            tmp = f.name
        try:
            settings = Settings()
            summary = ingest_path(Path(tmp), settings, "test")
            assert len(summary.results) == 1
            assert summary.results[0].status == "failed"
        finally:
            os.unlink(tmp)

    @pytest.mark.slow
    def test_directory_without_recursive(self) -> None:
        """空目录不递归时返回空结果（需要 sqlite-vec）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings()
            summary = ingest_path(Path(tmpdir), settings, "test")
            assert len(summary.results) == 0
            assert summary.failed == 0
