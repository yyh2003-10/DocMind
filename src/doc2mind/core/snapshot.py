"""Backend-neutral knowledge-base snapshot format.

Snapshots are deliberately separate from :mod:`core.backup`: a backup is a
SQLite recovery artifact, while a snapshot is an interchange document that a
different storage adapter can import.  The format is a ZIP containing a
manifest and deterministic UTF-8 JSONL members.  Vector data is optional and
treated as a cache; an importer may reindex when it cannot consume it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import struct
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SNAPSHOT_FORMAT = "docmind-snapshot"
SNAPSHOT_VERSION = 1
_MANIFEST = "manifest.json"
_MEMBERS = (
    "collections.jsonl",
    "documents.jsonl",
    "chunks.jsonl",
    "embeddings.jsonl",
)


class SnapshotError(RuntimeError):
    """Raised when a snapshot cannot be created or validated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _value_list(value: Any, dtype: str) -> list[float | int]:
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        if dtype == "int8":
            return list(struct.unpack(f"<{len(raw)}b", raw))
        if len(raw) % 4:
            raise SnapshotError("向量二进制长度不是 float32 的整数倍")
        return list(struct.unpack(f"<{len(raw) // 4}f", raw))
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, list | tuple):
        raise SnapshotError(f"无法序列化向量值: {type(value).__name__}")
    return [int(v) if dtype == "int8" else float(v) for v in value]


def _vec_dtype_and_dim(conn: sqlite3.Connection) -> tuple[str, int] | None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'vec_chunks'").fetchone()
    if not row or not row[0]:
        return None
    match = re.search(r"embedding\s+(INT8|FLOAT)\[(\d+)\]", str(row[0]), re.I)
    if not match:
        return None
    return ("int8" if match.group(1).upper() == "INT8" else "float32", int(match.group(2)))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute("SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1", (name,)).fetchone()
    )


def create_snapshot(
    db_path: Path,
    output_path: Path,
    *,
    embedding_model: str | None = None,
) -> dict[str, Any]:
    """Export stable knowledge records to a versioned interchange snapshot."""
    db_path, output_path = Path(db_path), Path(output_path)
    if not db_path.exists():
        raise SnapshotError(f"数据库不存在: {db_path}")
    if db_path.resolve() == output_path.resolve():
        raise SnapshotError("snapshot 输出路径不能覆盖当前数据库")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="docmind-snapshot-"):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Keep all members on one SQLite read snapshot while writers may
            # continue ingesting in another connection.
            conn.execute("BEGIN")
            members: dict[str, bytes] = {}
            collections = (
                [
                    {"name": str(row[0]), "created_at": str(row[1])}
                    for row in conn.execute(
                        "SELECT name, created_at FROM collections ORDER BY name"
                    )
                ]
                if _table_exists(conn, "collections")
                else []
            )
            members["collections.jsonl"] = _jsonl(collections)

            document_rows = conn.execute(
                "SELECT id, source, collection, format, file_hash, size_bytes, "
                "page_count, chunk_count, created_at, updated_at, title, tags, "
                "summary, enriched_at, deleted_at FROM documents ORDER BY id"
            ).fetchall()
            documents = [dict(row) for row in document_rows]
            members["documents.jsonl"] = _jsonl(documents)

            chunk_rows = conn.execute(
                "SELECT id, document_id, content, tokens, chunk_index, collection, "
                "source, format, doc_type, page, sheet, slide, heading, language, extra "
                "FROM chunks_meta ORDER BY id"
            ).fetchall()
            members["chunks.jsonl"] = _jsonl([dict(row) for row in chunk_rows])

            dtype_dim = _vec_dtype_and_dim(conn)
            embeddings: list[dict[str, Any]] = []
            if dtype_dim and _table_exists(conn, "vec_chunks"):
                # vec_chunks is a virtual table; a fresh connection must load
                # sqlite-vec before selecting its embedding column.
                from doc2mind.core.store.sqlite_vec import SqliteKnowledgeStore

                try:
                    SqliteKnowledgeStore._load_vec_extension(conn)
                except Exception as exc:  # noqa: BLE001
                    raise SnapshotError(f"无法加载 sqlite-vec 读取向量: {exc}") from exc
                dtype, dimension = dtype_dim
                for row in conn.execute("SELECT id, embedding FROM vec_chunks ORDER BY id"):
                    values = _value_list(row[1], dtype)
                    if len(values) != dimension:
                        raise SnapshotError(f"chunk {row[0]} 向量维度 {len(values)} != {dimension}")
                    embeddings.append(
                        {
                            "chunk_id": int(row[0]),
                            "dtype": dtype,
                            "dimension": dimension,
                            "values": values,
                        }
                    )
            members["embeddings.jsonl"] = _jsonl(embeddings)
        except sqlite3.Error as exc:
            raise SnapshotError(f"读取数据库失败: {exc}") from exc
        finally:
            conn.close()

        manifest: dict[str, Any] = {
            "format": SNAPSHOT_FORMAT,
            "version": SNAPSHOT_VERSION,
            "created_at": _now(),
            "source_backend": "sqlite",
            "embedding": (
                {
                    "dtype": dtype_dim[0],
                    "dimension": dtype_dim[1],
                    "model": embedding_model,
                }
                if dtype_dim
                else None
            ),
            "members": {
                name: {"bytes": len(data), "sha256": _sha256(data)}
                for name, data in members.items()
            },
            "counts": {
                "collections": len(collections),
                "documents": len(documents),
                "chunks": len(chunk_rows),
                "embeddings": len(embeddings),
            },
        }
        tmp_output = output_path.with_suffix(output_path.suffix + ".tmp")
        try:
            with zipfile.ZipFile(tmp_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    _MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
                )
                for name in _MEMBERS:
                    archive.writestr(name, members[name])
            os.replace(tmp_output, output_path)
        finally:
            tmp_output.unlink(missing_ok=True)
    manifest["path"] = str(output_path)
    return manifest


def inspect_snapshot(snapshot_path: Path) -> dict[str, Any]:
    """Validate a snapshot manifest, members, checksums, and JSONL syntax."""
    snapshot_path = Path(snapshot_path)
    if not snapshot_path.exists():
        raise SnapshotError(f"snapshot 文件不存在: {snapshot_path}")
    try:
        with zipfile.ZipFile(snapshot_path) as archive:
            names = set(archive.namelist())
            if _MANIFEST not in names:
                raise SnapshotError("snapshot 缺少 manifest.json")
            try:
                manifest = json.loads(archive.read(_MANIFEST).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise SnapshotError("snapshot manifest 损坏") from exc
            if manifest.get("format") != SNAPSHOT_FORMAT:
                raise SnapshotError("不支持的 snapshot 格式")
            if int(manifest.get("version", 0)) > SNAPSHOT_VERSION:
                raise SnapshotError("snapshot 版本高于当前客户端，请先升级 DocMind")
            declared = manifest.get("members")
            if not isinstance(declared, dict):
                raise SnapshotError("snapshot manifest 缺少 members")
            for name in _MEMBERS:
                if name not in names or name not in declared:
                    raise SnapshotError(f"snapshot 缺少成员: {name}")
                data = archive.read(name)
                spec = declared[name]
                if int(spec.get("bytes", -1)) != len(data) or spec.get("sha256") != _sha256(data):
                    raise SnapshotError(f"snapshot 成员校验失败: {name}")
                for line in data.decode("utf-8").splitlines():
                    if line:
                        json.loads(line)
    except zipfile.BadZipFile as exc:
        raise SnapshotError("snapshot 不是有效 ZIP 文件") from exc
    manifest["path"] = str(snapshot_path)
    return manifest
