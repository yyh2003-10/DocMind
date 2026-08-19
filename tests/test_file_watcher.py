"""文件系统监控模块 FileWatcher 单元测试。"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from doc2mind.core.config import Settings
from doc2mind.core.file_watcher import FileWatcher


def test_no_watchdog_graceful(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """watchdog 未安装时 start() 优雅降级，不抛异常。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: tuple, **kwargs: dict) -> object:
        if "watchdog" in name:
            raise ImportError("No module named 'watchdog'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    s = Settings(db_path=tmp_path / "test.db")
    watcher = FileWatcher(paths=[str(tmp_path)], settings=s)
    watcher.start()
    assert not watcher.is_running
    watcher.stop()


def test_supported_file_filters(tmp_path: Path) -> None:
    """测试临时文件/不支持格式被过滤。"""
    pytest.importorskip("watchdog")

    s = Settings(db_path=tmp_path / "test.db")
    ingested_events: list[dict] = []
    watcher = FileWatcher(
        paths=[str(tmp_path)],
        settings=s,
        debounce_seconds=0.1,
        on_ingested=lambda p: ingested_events.append(p),
    )

    # 模拟触发临时文件
    watcher._schedule_ingest(str(tmp_path / ".hidden.md"))
    watcher._schedule_ingest(str(tmp_path / "file.tmp"))
    watcher._schedule_ingest(str(tmp_path / "~$word.docx"))
    watcher._schedule_ingest(str(tmp_path / "unknown.xyz"))

    # pending_timers 应该为空（未被 schedule）
    assert len(watcher._pending_timers) == 0


def test_debounce_merges(tmp_path: Path) -> None:
    """测试短时间内多次修改同一个文件防抖合并。"""
    s = Settings(db_path=tmp_path / "test.db")

    target_file = tmp_path / "doc.md"
    target_file.write_text("hello world", encoding="utf-8")

    watcher = FileWatcher(
        paths=[str(tmp_path)],
        settings=s,
        debounce_seconds=0.2,
    )

    with patch("doc2mind.core.file_watcher.ingest_path") as mock_ingest:
        mock_resp = MagicMock()
        mock_resp.ingested = []
        mock_resp.failed = 0
        mock_ingest.return_value = mock_resp

        # 连续触发 3 次
        watcher._schedule_ingest(str(target_file))
        time.sleep(0.05)
        watcher._schedule_ingest(str(target_file))
        time.sleep(0.05)
        watcher._schedule_ingest(str(target_file))

        # 应该只有一个 pending timer
        assert len(watcher._pending_timers) == 1

        # 等待定时器触发
        time.sleep(0.3)
        assert mock_ingest.call_count == 1
        watcher.stop()
