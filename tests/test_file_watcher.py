"""文件系统监控模块 FileWatcher 单元测试。"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from doc2mind.core.config import Settings
from doc2mind.core.file_watcher import FileWatcher
from doc2mind.core.loader.base import make_source, stream_file_hash


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

    # 待处理集合应该为空（未被 schedule）
    assert len(watcher._pending) == 0
    watcher.stop()


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
        mock_resp = SimpleNamespace(
            results=[], failed=0, curatable_document_ids=[]
        )
        mock_ingest.return_value = mock_resp

        # 连续触发 3 次
        watcher._schedule_ingest(str(target_file))
        time.sleep(0.05)
        watcher._schedule_ingest(str(target_file))
        time.sleep(0.05)
        watcher._schedule_ingest(str(target_file))

        # 去抖窗口内：待处理集合只有 1 个条目
        assert len(watcher._pending) == 1

        # 等待去抖到期 + worker 消费
        time.sleep(0.4)
        assert mock_ingest.call_count == 1
        watcher.stop()


def test_deleted_source_emits_event_without_removing_document(tmp_path: Path) -> None:
    source_path = tmp_path / "deleted.md"
    source_path.write_text("indexed", encoding="utf-8")
    source = make_source(source_path)
    doc = SimpleNamespace(
        id="doc-1", source=source, collection="default", file_hash="hash-1"
    )

    class FakeStore:
        def find_documents_by_source(self, value: str):
            return [doc] if value == source else []

    source_path.unlink()
    events: list[dict] = []
    watcher = FileWatcher(
        paths=[],
        settings=Settings(db_path=tmp_path / "unused.db"),
        on_ingested=events.append,
    )
    watcher._emit_source_missing(source_path, FakeStore())  # type: ignore[arg-type]

    assert events[0]["type"] == "file_source_missing"
    assert events[0]["document_id"] == "doc-1"
    assert events[0]["path"] == source


def test_move_with_same_hash_updates_source_without_reingesting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_path = tmp_path / "old.md"
    new_path = tmp_path / "renamed.md"
    old_path.write_text("same content", encoding="utf-8")
    file_hash, _ = stream_file_hash(old_path)
    old_source = make_source(old_path)
    new_source = make_source(new_path)
    doc = SimpleNamespace(
        id="doc-1", source=old_source, collection="default", file_hash=file_hash
    )

    class FakeStore:
        def find_documents_by_source(self, value: str):
            return [doc] if value == doc.source else []

        def relocate_documents(self, old: str, new: str, digest: str) -> int:
            assert (old, new, digest) == (old_source, new_source, file_hash)
            doc.source = new
            return 1

    old_path.rename(new_path)
    events: list[dict] = []
    watcher = FileWatcher(
        paths=[],
        settings=Settings(db_path=tmp_path / "unused.db"),
        on_ingested=events.append,
        store_provider=lambda: FakeStore(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        "doc2mind.core.file_watcher.ingest_path",
        lambda *args, **kwargs: pytest.fail("相同内容移动不应重新摄入"),
    )

    watcher._handle_moved(old_path, new_path)

    assert doc.source == new_source
    assert events == [
        {
            "type": "file_moved",
            "path": new_source,
            "old_path": old_source,
            "result": "source_updated",
            "document_id": "doc-1",
        }
    ]


def test_move_with_changed_hash_forces_ingest_even_if_hash_exists_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_path = tmp_path / "old.md"
    new_path = tmp_path / "moved.md"
    old_path.write_text("old content", encoding="utf-8")
    old_hash, _ = stream_file_hash(old_path)
    new_path.write_text("content already indexed at another path", encoding="utf-8")
    old_source = make_source(old_path)
    doc = SimpleNamespace(
        id="doc-old", source=old_source, collection="default", file_hash=old_hash
    )

    class FakeStore:
        def find_documents_by_source(self, value: str):
            return [doc] if value == old_source else []

    ingest_calls: list[dict] = []

    def fake_ingest(*args, **kwargs):
        ingest_calls.append(kwargs)
        return SimpleNamespace(
            results=[SimpleNamespace(status="ingested", document_id="doc-new", error=None)],
            failed=0,
            curatable_document_ids=[],
        )

    monkeypatch.setattr("doc2mind.core.file_watcher.ingest_path", fake_ingest)
    events: list[dict] = []
    watcher = FileWatcher(
        paths=[],
        settings=Settings(db_path=tmp_path / "unused.db"),
        on_ingested=events.append,
        store_provider=lambda: FakeStore(),  # type: ignore[arg-type]
    )

    old_path.unlink()
    watcher._handle_moved(old_path, new_path)

    assert ingest_calls and ingest_calls[0]["force"] is True
    assert any(event["type"] == "file_source_missing" for event in events)
    assert any(
        event["type"] == "file_ingested"
        and event["path"] == str(new_path.resolve())
        and event["result"] == "ingested"
        for event in events
    )


def test_move_hash_read_failure_reports_actionable_destination_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_path = tmp_path / "old.md"
    new_path = tmp_path / "moved.md"
    old_path.write_text("old content", encoding="utf-8")
    old_source = make_source(old_path)
    doc = SimpleNamespace(
        id="doc-old", source=old_source, collection="default", file_hash="old-hash"
    )

    class FakeStore:
        def find_documents_by_source(self, value: str):
            return [doc] if value == old_source else []

    old_path.unlink()
    new_path.write_text("new content", encoding="utf-8")
    monkeypatch.setattr(
        "doc2mind.core.file_watcher.stream_file_hash",
        lambda _path: (_ for _ in ()).throw(PermissionError("access denied")),
    )
    events: list[dict] = []
    watcher = FileWatcher(
        paths=[],
        settings=Settings(db_path=tmp_path / "unused.db"),
        on_ingested=events.append,
        store_provider=lambda: FakeStore(),  # type: ignore[arg-type]
    )

    watcher._handle_moved(old_path, new_path)

    failure = next(
        event for event in events if event["type"] == "file_ingested"
    )
    assert failure["path"] == str(new_path.resolve())
    assert failure["result"] == "failed"
    assert "access denied" in failure["error"]
    assert "权限" in failure["error"]
