"""摄入流水线 — 把 loader→chunker→embedder→store 串起来。

入口：`ingest_path(path, settings, collection, force)` 返回 `IngestResult`
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doc2mind.core.chunker import chunk_document
from doc2mind.core.config import Settings, get_settings
from doc2mind.core.embedder import get_embedder
from doc2mind.core.loader.detect import get_loader
from doc2mind.core.models import (
    DocFormat,
    DocumentElement,
    ElementType,
    LoadedDocument,
)
from doc2mind.core.retriever.search import StoredChunkMeta
from doc2mind.core.store.sqlite_vec import (
    StoredDocument,
    VectorStore,
)

logger = logging.getLogger("doc2mind.pipeline")


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
    progress: "Callable[[int, int], None] | None" = None,
) -> IngestSummary:
    """摄入一个文件或目录。

    Args:
        path: 文件或目录
        settings: 配置
        collection: 集合名
        recursive: 目录是否递归
        force: 即使 file_hash 已存在也重新摄入
        store: 已打开的 VectorStore；None 则内部创建并关闭
        progress: 进度回调 (done, total)，每处理完一个文件调用一次；None 表示不回调

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

    total = len(files)
    summary = IngestSummary()
    try:
        for idx, f in enumerate(files, start=1):
            res = _ingest_one(f, settings, collection, force, store, embedder)
            summary.results.append(res)
            if res.status == "ingested":
                summary.total_documents += 1
                summary.total_chunks += res.chunk_count
            elif res.status == "skipped":
                summary.skipped += 1
            elif res.status == "failed":
                summary.failed += 1
            if progress is not None:
                progress(idx, total)
        logger.info(
            "ingest 完成: 路径=%s collection=%s ingested=%d skipped=%d failed=%d 总文档=%d 总chunks=%d",
            path, collection, summary.total_documents, summary.skipped,
            summary.failed, summary.total_documents, summary.total_chunks,
        )
        return summary
    finally:
        if owns_store:
            store.close()


def ingest_text(
    text: str,
    title: str = "",
    collection: str = "default",
    force: bool = False,
    store: VectorStore | None = None,
) -> IngestResult:
    """直接摄入一段文本（AI 沉淀经验/笔记用，不依赖文件路径）。

    Args:
        text: 文本内容（经验、结论、要点等）
        title: 标题（可选）；留空则用文本开头自动生成
        collection: 集合名
        force: 相同内容（MD5）是否强制重新摄入
        store: 已打开的 VectorStore；None 则内部创建并关闭

    Returns:
        `IngestResult`（status: ingested | skipped | failed）
    """
    t0 = time.perf_counter()
    stripped = (text or "").strip()
    if not stripped:
        return IngestResult(
            source=title or "(空文本)", collection=collection, format="md",
            size_bytes=0, chunk_count=0, elapsed_ms=0,
            status="failed", error="文本内容为空",
        )

    settings = get_settings()
    settings.ensure_dirs()

    embedder = get_embedder(settings)
    owns_store = store is None
    if store is None:
        store = VectorStore(settings.db_path, embedder.dimension)
        store.open()

    try:
        # 构造 LoadedDocument：标题作 heading，正文按行拆 paragraph
        final_title = (title or "").strip() or f"note:{stripped[:24]}"
        elements: list[DocumentElement] = []
        if title.strip():
            elements.append(DocumentElement(
                content=final_title,
                type=ElementType.HEADING,
                metadata={"level": 1},
            ))
        for line in stripped.splitlines():
            line = line.strip()
            if line:
                elements.append(DocumentElement(
                    content=line, type=ElementType.PARAGRAPH,
                ))
        if not elements:
            elements.append(DocumentElement(
                content=stripped, type=ElementType.PARAGRAPH,
            ))

        file_hash = hashlib.md5(stripped.encode("utf-8")).hexdigest()
        doc = LoadedDocument(
            source=final_title,
            format=DocFormat.MARKDOWN,
            elements=elements,
            page_count=None,
            size_bytes=len(stripped.encode("utf-8")),
            file_hash=file_hash,
        )

        # 增量去重：同内容文本已入库则跳过
        if not force:
            existing = store.find_document_id_by_hash(file_hash, collection)
            if existing is not None:
                logger.info(
                    "ingest_text 跳过(已存在): title=%s collection=%s doc_id=%s",
                    final_title, collection, existing,
                )
                return IngestResult(
                    source=final_title, collection=collection, format="md",
                    size_bytes=doc.size_bytes, chunk_count=0,
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                    status="skipped", document_id=existing,
                )

        # 分块 + 嵌入
        from doc2mind.core.chunker.base import ChunkerError

        try:
            chunks = chunk_document(doc, settings)
        except ChunkerError as e:
            return _fail(Path(final_title), collection, f"分块失败: {e}", t0)
        if not chunks:
            return _fail(Path(final_title), collection, "无有效内容", t0)

        try:
            embeddings = list(embedder.embed(chunks))
        except Exception as e:  # noqa: BLE001
            return _fail(Path(final_title), collection, f"嵌入失败: {e}", t0)
        if len(embeddings) != len(chunks):
            return _fail(
                Path(final_title), collection,
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
                    source=final_title,
                    collection=collection,
                    format=doc.format.value,
                    file_hash=file_hash,
                    size_bytes=doc.size_bytes,
                    page_count=None,
                    chunk_count=len(chunks),
                    created_at=now,
                    updated_at=now,
                )
            )
            store.insert_chunks(
                document_id=document_id,
                collection=collection,
                source=final_title,
                fmt=doc.format.value,
                chunks=chunks,
                embeddings=embeddings,
            )
        except Exception as e:  # noqa: BLE001
            return _fail(Path(final_title), collection, f"写库失败: {e}", t0)

        logger.info(
            "ingest_text 完成: title=%s collection=%s chunks=%d",
            final_title, collection, len(chunks),
        )
        return IngestResult(
            source=final_title, collection=collection,
            format="md", size_bytes=doc.size_bytes,
            chunk_count=len(chunks),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            status="ingested", document_id=document_id,
        )
    finally:
        if owns_store:
            store.close()


def ingest_text(
    text: str,
    title: str | None = None,
    collection: str = "default",
    force: bool = False,
    settings: Settings | None = None,
    store: VectorStore | None = None,
) -> IngestResult:
    """摄入一段纯文本（AI 沉淀经验 / 手工笔记），不走文件系统。

    与 `ingest_path` 的区别：输入是字符串而非路径，适合 agent 把
    对话中得到的经验、结论、代码片段直接写入知识库，供后续检索。
    按文本 MD5 去重：内容完全相同的文本再次摄入会被跳过；
    force=True 时跳过去重，强制重新摄入。

    Args:
        text: 要入库的文本内容。
        title: 可选标题，作为 source 显示（如 `note:标题`）；空则取文本前 30 字符。
        collection: 集合名。
        force: 即使相同文本已存在也重新摄入（默认 False = 跳过去重）。
        settings: 配置；None 则用全局配置。
        store: 已打开的 VectorStore；None 则内部创建并关闭。

    Returns:
        `IngestResult`
    """
    if settings is None:
        settings = get_settings()
    settings.ensure_dirs()

    body = (text or "").strip()
    if not body:
        return IngestResult(
            source="note:(空)", collection=collection, format="md",
            size_bytes=0, chunk_count=0, elapsed_ms=0,
            status="failed", error="文本内容为空",
        )

    display = (title or body[:30]).strip() or "未命名经验"
    source = f"note:{display}"
    doc = LoadedDocument(
        source=source,
        format=DocFormat.MARKDOWN,
        elements=[
            DocumentElement(
                content=body,
                type=ElementType.PARAGRAPH,
                metadata={"source_format": DocFormat.MARKDOWN.value},
            )
        ],
        page_count=None,
        size_bytes=len(body.encode("utf-8")),
        file_hash=hashlib.md5(body.encode("utf-8")).hexdigest(),
    )

    t0 = time.perf_counter()
    embedder = get_embedder(settings)
    owns_store = store is None
    if store is None:
        store = VectorStore(settings.db_path, embedder.dimension)
        store.open()

    try:
        # 增量去重：同一段文本已存在则跳过（force=True 时跳过检查，强制重新摄入）
        if not force:
            existing = store.find_document_id_by_hash(doc.file_hash, collection)
            if existing is not None:
                logger.info(
                    "ingest_text 跳过(已存在): source=%s collection=%s doc_id=%s",
                    source, collection, existing,
                )
                return IngestResult(
                    source=source, collection=collection,
                    format=doc.format.value, size_bytes=doc.size_bytes,
                    chunk_count=0,
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                    status="skipped", document_id=existing,
                )

        try:
            chunks = chunk_document(doc, settings)
        except Exception as e:  # noqa: BLE001
            return _fail(Path(source), collection, f"分块失败: {e}", t0)

        if not chunks:
            return _fail(Path(source), collection, "无有效内容", t0)

        try:
            embeddings = list(embedder.embed(chunks))
        except Exception as e:  # noqa: BLE001
            return _fail(Path(source), collection, f"嵌入失败: {e}", t0)

        if len(embeddings) != len(chunks):
            return _fail(
                Path(source), collection,
                f"嵌入数量 ({len(embeddings)}) 与分块数量 ({len(chunks)}) 不一致",
                t0,
            )

        document_id = uuid.uuid4().hex
        now = _now_iso()
        # UNIQUE(collection, source)：同标题重入（force 或内容变化）时先删旧文档，
        # 避免插入新 id 撞唯一约束；删除会连带清理旧分块与向量。
        try:
            store.delete_by_source(doc.source, collection)
        except Exception:  # noqa: BLE001
            pass  # 无旧文档时 delete_by_source 返回 0，不抛错
        store.upsert_document(
            StoredDocument(
                id=document_id,
                source=doc.source,
                collection=collection,
                format=doc.format.value,
                file_hash=doc.file_hash,
                size_bytes=doc.size_bytes,
                page_count=None,
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
        logger.info(
            "ingest_text 完成: source=%s collection=%s chunks=%d",
            source, collection, len(chunks),
        )
        return IngestResult(
            source=source, collection=collection,
            format=doc.format.value, size_bytes=doc.size_bytes,
            chunk_count=len(chunks),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            status="ingested", document_id=document_id,
        )
    except Exception as e:  # noqa: BLE001
        return _fail(Path(source), collection, f"写库失败: {e}", t0)
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
        existing = store.find_document_id_by_hash(doc.file_hash, collection)
        if existing is not None:
            logger.info(
                "ingest 跳过(已存在): source=%s collection=%s doc_id=%s",
                doc.source, collection, existing,
            )
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
        # UNIQUE(collection, source)：文件内容变化（MD5 变）后重新导入同一文件时，
        # 旧文档（同 source 不同 hash）仍在库中，插入新 id 会撞唯一约束。
        # 先删除同 (collection, source) 的旧文档（连带旧分块与向量），实现"替换"语义。
        try:
            store.delete_by_source(doc.source, collection)
        except Exception:  # noqa: BLE001
            pass  # 无旧文档时 delete_by_source 返回 0，不抛错
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
    logger.error("ingest 失败: source=%s collection=%s 原因: %s", Path(path).name, collection, err)
    return IngestResult(
        source=Path(path).name, collection=collection, format="unknown",
        size_bytes=0, chunk_count=0,
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
        status="failed", error=err,
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
