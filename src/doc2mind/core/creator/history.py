"""Small, durable Artifact draft/version history.

Versions are append-only JSON records. Keeping the source content alongside
the export metadata means a failed export never destroys the user's draft and
an artifact can be regenerated later without scraping a generated file.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ArtifactHistoryError(RuntimeError):
    """Raised when an artifact history cannot be read or written."""


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_id(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ArtifactHistoryError("artifact_id 不能为空")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _display_id(value: str) -> str:
    return (value or "").strip()


def _lock_for(root: Path, artifact_id: str) -> threading.Lock:
    key = f"{Path(root).expanduser().resolve()}::{artifact_id}"
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _path(root: Path, artifact_id: str) -> Path:
    return Path(root).expanduser() / f"{_safe_id(artifact_id)}.json"


def _read(root: Path, artifact_id: str) -> list[dict[str, Any]]:
    path = _path(root, artifact_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactHistoryError(f"无法读取 Artifact 历史: {exc}") from exc
    if not isinstance(data, list):
        raise ArtifactHistoryError("Artifact 历史文件格式无效")
    return [item for item in data if isinstance(item, dict)]


def save_version(
    root: Path,
    artifact_id: str,
    *,
    content: str,
    artifact_format: str | None = None,
    title: str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not content or not content.strip():
        raise ArtifactHistoryError("Artifact 内容不能为空")
    root = Path(root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    with _lock_for(root, artifact_id):
        versions = _read(root, artifact_id)
        version = len(versions) + 1
        created_at = _now()
        record = {
            "version_id": hashlib.sha256(f"{artifact_id}:{version}:{created_at}".encode()).hexdigest()[:16],
            "artifact_id": _display_id(artifact_id),
            "version": version,
            "created_at": created_at,
            "format": artifact_format,
            "title": title,
            "content": content,
            "sources": sources or [],
        }
        versions.append(record)
        path = _path(root, artifact_id)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(versions, stream, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        finally:
            Path(tmp_name).unlink(missing_ok=True)
    return {key: value for key, value in record.items() if key != "content"} | {"content": content}


def list_versions(root: Path, artifact_id: str) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in item.items() if key != "content"}
        for item in reversed(_read(Path(root).expanduser(), artifact_id))
    ]


def get_version(root: Path, artifact_id: str, version_id: str) -> dict[str, Any] | None:
    for item in _read(Path(root).expanduser(), artifact_id):
        if item.get("version_id") == version_id:
            return item
    return None
