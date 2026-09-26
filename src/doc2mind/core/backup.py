"""Knowledge-base backup, restore, and diagnostic bundle utilities.

The database contains the document metadata/chunks, vectors, graph data, and
chat history.  Backups are zip files with a small manifest and a SQLite copy.
The SQLite online-backup API is used instead of copying a live WAL database.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKUP_FORMAT = "docmind-backup"
BACKUP_VERSION = 1
_DB_MEMBER = "docmind.db"
_MANIFEST_MEMBER = "manifest.json"


class BackupError(RuntimeError):
    """Raised when a backup cannot be created or restored safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _copy_sqlite(source: Path, destination: Path) -> None:
    if not source.exists():
        raise BackupError(f"数据库不存在: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        src = sqlite3.connect(str(source), timeout=30)
        dst = sqlite3.connect(str(destination), timeout=30)
        try:
            src.backup(dst)
            row = dst.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise BackupError(f"数据库完整性校验失败: {row[0] if row else 'unknown'}")
        finally:
            dst.close()
            src.close()
    except sqlite3.Error as exc:
        raise BackupError(f"读取数据库失败: {exc}") from exc


def create_backup(db_path: Path, output_path: Path) -> dict[str, Any]:
    """Create a consistent zip backup and return its manifest."""
    db_path = Path(db_path)
    output_path = Path(output_path)
    try:
        if output_path.resolve() == db_path.resolve():
            raise BackupError("备份输出路径不能覆盖当前数据库")
    except OSError:
        # 非现有路径仍可继续，后续 mkdir/replace 会给出明确错误。
        pass
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": _now(),
        "database_name": db_path.name,
    }
    with tempfile.TemporaryDirectory(prefix="docmind-backup-") as temp:
        snapshot = Path(temp) / _DB_MEMBER
        _copy_sqlite(db_path, snapshot)
        manifest["database_size"] = snapshot.stat().st_size
        manifest["tables"] = _table_names(snapshot)
        tmp_output = output_path.with_suffix(output_path.suffix + ".tmp")
        try:
            with zipfile.ZipFile(tmp_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(_MANIFEST_MEMBER, json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.write(snapshot, _DB_MEMBER)
            os.replace(tmp_output, output_path)
        finally:
            if tmp_output.exists():
                tmp_output.unlink(missing_ok=True)
    manifest["path"] = str(output_path)
    return manifest


def _table_names(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table') ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def inspect_backup(backup_path: Path) -> dict[str, Any]:
    """Validate a backup without changing the active database."""
    backup_path = Path(backup_path)
    if not backup_path.exists():
        raise BackupError(f"备份文件不存在: {backup_path}")
    with zipfile.ZipFile(backup_path) as archive:
        names = set(archive.namelist())
        if _MANIFEST_MEMBER not in names or _DB_MEMBER not in names:
            raise BackupError("不是有效的 DocMind 备份：缺少 manifest.json 或 docmind.db")
        try:
            manifest = json.loads(archive.read(_MANIFEST_MEMBER).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise BackupError("备份清单损坏") from exc
        if manifest.get("format") != BACKUP_FORMAT:
            raise BackupError("不支持的备份格式")
        if int(manifest.get("version", 0)) > BACKUP_VERSION:
            raise BackupError("备份版本高于当前客户端，请先升级 DocMind")
        with tempfile.TemporaryDirectory(prefix="docmind-backup-check-") as temp:
            extracted = Path(temp) / _DB_MEMBER
            _safe_extract_member(archive, _DB_MEMBER, extracted)
            _validate_sqlite(extracted)
            manifest["database_size"] = extracted.stat().st_size
    manifest["path"] = str(backup_path)
    return manifest


def restore_backup(db_path: Path, backup_path: Path) -> dict[str, Any]:
    """Restore a validated backup, retaining a timestamped pre-restore copy."""
    manifest = inspect_backup(backup_path)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="docmind-restore-") as temp:
        restored = Path(temp) / _DB_MEMBER
        with zipfile.ZipFile(backup_path) as archive:
            _safe_extract_member(archive, _DB_MEMBER, restored)
        _validate_sqlite(restored)
        previous: str | None = None
        if db_path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            previous_path = db_path.with_name(
                f"{db_path.stem}.pre-restore-{stamp}{db_path.suffix}"
            )
            # Preserve the pre-restore state through SQLite's online backup
            # API as well; copying only the main file would omit live WAL data.
            _copy_sqlite(db_path, previous_path)
            previous = str(previous_path)
        replacement = db_path.with_suffix(db_path.suffix + ".restore-tmp")
        shutil.copy2(restored, replacement)
        try:
            os.replace(replacement, db_path)
        except OSError as exc:
            replacement.unlink(missing_ok=True)
            raise BackupError(
                "无法替换当前数据库；请先停止正在运行的导入、重建或其他 DocMind 实例后重试"
            ) from exc
        # A stale WAL/rollback journal belongs to the previous main database
        # and must not be replayed when the restored database opens. This runs
        # only after Windows has accepted the atomic replacement, so a locked
        # database is left untouched on failure.
        try:
            for suffix in ("-wal", "-shm", "-journal"):
                Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        except OSError as exc:
            raise BackupError(
                "数据库主文件已恢复，但无法清理旧的 SQLite 日志文件；请停止其他 DocMind 实例后重新恢复"
            ) from exc
    return {"restored": True, "path": str(db_path), "previous_backup": previous, "manifest": manifest}


def _validate_sqlite(path: Path) -> None:
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise BackupError(f"数据库完整性校验失败: {row[0] if row else 'unknown'}")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise BackupError(f"备份中的数据库不可读: {exc}") from exc


def _safe_extract_member(archive: zipfile.ZipFile, name: str, destination: Path) -> None:
    info = archive.getinfo(name)
    if info.is_dir() or Path(name).name != name:
        raise BackupError("备份包含非法文件路径")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)
