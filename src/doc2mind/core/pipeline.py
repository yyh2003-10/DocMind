"""摄入流水线 — 把 loader→chunker→embedder→store 串起来。

入口：`ingest_path(path, settings, collection, force)` 返回 `IngestResult`
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doc2mind.core.chunker import chunk_document
from doc2mind.core.config import Settings, get_settings
from doc2mind.core.embedder import get_embedder
from doc2mind.core.loader.detect import get_loader
from doc2mind.core.models import LoadedDocument
from doc2mind.core.retriever.search import StoredChunkMeta
from doc2mind.core.store.sqlite_vec import (
    StoredDocument,
    VectorStore,
)


@dataclass(frozen=True)
class IngestResult:
    """单次摄入结果。"""

    source: str
    collection: str
    format: str
    size_bytes: int
    chunk_count: int
    elapsed_ms: int
    status: str  # ingested | skipped | updated | failed
    error: str | None = None
    document_id: str | None = None


@dataclass
class IngestSummary:
    """批量摄入汇总。"""

    results: list[IngestResult] = field(default_factory=list)
    total_documents: int = 0
    total_chunks: int = 0
    skipped: int = 0
    failed: int = 0


def ingest_path(
    path: Path,
    settings: Settings | None = None,
    collection: str = "default",
    recursive: bool = False,
    force: bool = False,
    store: VectorStore | None = None,
) -> IngestSummary:
    """摄入一个文件或目录。

    Args:
        path: 文件或目录
        settings: 配置
        collection: 集合名
        recursive: 目录是否递归
        force: 即使 file_hash 已存在也重新摄入
        store: 已打开的 VectorStore；None 则内部创建并关闭

    Returns:
        `IngestSummary`
    """
    if settings is None:
        settings = get_settings()
    settings.ensure_dirs()

    # 收集待摄入文件
    files: list[Path] = []
    p = Path(path)
    if p.is_file():
        files.append(p)
    elif p.is_dir():
        if recursive:
            files = sorted(f for f in p.rglob("*") if f.is_file())
        else:
            files = sorted(f for f in p.iterdir() if f.is_file())
    else:
        return IngestSummary(results=[
            IngestResult(
                source=str(path), collection=collection, format="unknown",
                size_bytes=0, chunk_count=0, elapsed_ms=0, status="failed",
                error=f"路径不存在: {path}",
            )
        ])

    embedder = get_embedder(settings)
    owns_store = store is None
    if store is None:
        store = VectorStore(settings.db_path, embedder.dimension)
        store.open()

    summary = IngestSummary()
    try:
        for f in files:
            res = _ingest_one(f, settings, collection, force, store, embedder)
            summary.results.append(res)
            if res.status == "ingested":
                summary.total_documents += 1
                summary.total_chunks += res.chunk_count
            elif res.status == "skipped":
                summary.skipped += 1
            elif res.status == "failed":
                summary.failed += 1
        return summary
    finally:
        if owns_store:
            store.close()


def _ingest_one(
    path: Path,
    settings: Settings,
    collection: str,
    force: bool,
    store: VectorStore,
    embedder,
) -> IngestResult:
    """摄入单个文件。"""
    t0 = time.perf_counter()
    try:
        loader = get_loader(path)
    except Exception as e:  # noqa: BLE001
        return _fail(path, collection, str(e), t0)

    try:
        doc = loader.extract(path)
    except Exception as e:  # noqa: BLE001
        return _fail(path, collection, f"加载失败: {e}", t0)

    # 增量去重：file_hash 已存在则跳过
    if not force:
        existing = _find_by_hash(store, doc.file_hash, collection)
        if existing is not None:
            return IngestResult(
                source=doc.source, collection=collection,
                format=doc.format.value, size_bytes=doc.size_bytes,
                chunk_count=0,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
                status="skipped", document_id=existing,
            )

    # 分块
    from doc2mind.core.chunker.base import ChunkerError

    try:
        chunks = chunk_document(doc, settings)
    except ChunkerError as e:
        return _fail(path, collection, f"分块失败: {e}", t0)

    if not chunks:
        return _fail(path, collection, "无有效内容", t0)

    # 嵌入
    try:
        embeddings = list(embedder.embed(chunks))
    except Exception as e:  # noqa: BLE001
        return _fail(path, collection, f"嵌入失败: {e}", t0)

    if len(embeddings) != len(chunks):
        return _fail(
            path, collection,
            f"嵌入数量 ({len(embeddings)}) 与分块数量 ({len(chunks)}) 不一致",
            t0,
        )

    # 写库
    document_id = uuid.uuid4().hex
    now = _now_iso()
    try:
        store.upsert_document(
            StoredDocument(
                id=document_id,
                source=doc.source,
                collection=collection,
                format=doc.format.value,
                file_hash=doc.file_hash,
                size_bytes=doc.size_bytes,
                page_count=doc.page_count,
                chunk_count=len(chunks),
                created_at=now,
                updated_at=now,
            )
        )
        store.insert_chunks(
            document_id=document_id,
            collection=collection,
            source=doc.source,
            fmt=doc.format.value,
            chunks=chunks,
            embeddings=embeddings,
        )
    except Exception as e:  # noqa: BLE001
        return _fail(path, collection, f"写库失败: {e}", t0)

    return IngestResult(
        source=doc.source, collection=collection,
        format=doc.format.value, size_bytes=doc.size_bytes,
        chunk_count=len(chunks),
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
        status="ingested", document_id=document_id,
    )


def _fail(path: Path, collection: str, err: str, t0: float) -> IngestResult:
    return IngestResult(
        source=Path(path).name, collection=collection, format="unknown",
        size_bytes=0, chunk_count=0,
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
        status="failed", error=err,
    )


def _find_by_hash(
    store: VectorStore, file_hash: str, collection: str
) -> str | None:
    """按 file_hash 找已存在的文档 ID。"""
    import sqlite3

    try:
        conn = store._conn  # noqa: SLF001 — 内部访问
        if conn is None:
            return None
        row = conn.execute(
            "SELECT id FROM documents WHERE file_hash = ? AND collection = ?",
            (file_hash, collection),
        ).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
