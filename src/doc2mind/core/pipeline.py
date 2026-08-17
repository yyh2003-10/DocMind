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
    # 入库后 AI 自动整理的结果（enrich/categorize）；未触发或失败时为 None。
    # collection 字段反映整理后的最终集合（可能被自动归类移动过）。
    curation: dict | None = None


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
    progress: Callable[[int, int], None] | None = None,
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
    # 入库自动整理护栏：目录文件数超上限时跳过（一次目录摄入触发数百次
    # LLM 调用既慢又贵），此时应改用 curate 工具/接口批量整理。
    auto_curate = bool(
        getattr(settings, "auto_curate_on_ingest", False)
        and total <= getattr(settings, "curate_auto_max_files", 20)
    )
    if getattr(settings, "auto_curate_on_ingest", False) and not auto_curate:
        logger.info(
            "文件数 %d 超过 curate_auto_max_files=%d，跳过入库自动整理"
            "（可用 curate 批量整理）",
            total, getattr(settings, "curate_auto_max_files", 20),
        )
    summary = IngestSummary()
    try:
        for idx, f in enumerate(files, start=1):
            res = _ingest_one(f, settings, collection, force, store, embedder,
                              auto_curate=auto_curate)
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
    title: str | None = None,
    collection: str | None = None,
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
        collection: 集合名。None（默认）= 落到默认集合并允许 AI 自动归类
            （auto_curate_on_ingest 开启且 LLM 可用时，入库后自动移动到
            AI 判断的集合）；显式指定 = 尊重调用方选择，只打标签不归类。
        force: 即使相同文本已存在也重新摄入（默认 False = 跳过去重）。
        settings: 配置；None 则用全局配置。
        store: 已打开的 VectorStore；None 则内部创建并关闭。

    Returns:
        `IngestResult`（collection 为整理后的最终集合；curation 携带整理结果）
    """
    if settings is None:
        settings = get_settings()
    settings.ensure_dirs()

    # None = 未指定集合 → 默认集合 + 允许 AI 自动归类
    auto_categorize = collection is None
    effective_collection = (
        (collection or settings.collection_default or "default").strip() or "default"
    )
    collection = effective_collection

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

        # 维度预检：与文件导入同规则（换模型未重建索引时给出可操作指引）
        store_dim = getattr(store, "embedding_dim", None)
        embed_dim = getattr(embedder, "dimension", None)
        if store_dim is not None and embed_dim is not None and embed_dim != store_dim:
            return _fail(
                Path(source), collection,
                f"嵌入模型维度 ({embed_dim}) 与向量库维度 ({store_dim}) 不一致："
                "请先在设置页执行「重建索引」（reindex），或切回原嵌入模型后再导入",
                t0,
            )

        document_id = uuid.uuid4().hex
        now = _now_iso()
        # 单事务原子替换（删旧 → 写文档 → 写分块）：失败整体回滚，
        # 不会留下"文档记录存在但没有任何分块"的孤儿状态。
        try:
            store.replace_document(
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
                ),
                chunks=chunks,
                embeddings=embeddings,
            )
        except Exception as e:  # noqa: BLE001
            return _fail(Path(source), collection, f"写库失败: {e}", t0)
        logger.info(
            "ingest_text 完成: source=%s collection=%s chunks=%d",
            source, collection, len(chunks),
        )
        # 入库自动整理：打标签/摘要（+ 未指定集合时自动归类）。
        # 失败不影响入库结果；归类可能移动集合，最终值以库里为准。
        curation = _auto_curate_after_ingest(
            store, settings, document_id, allow_categorize=auto_categorize
        )
        final_doc = store.get_document_by_id(document_id)
        return IngestResult(
            source=source,
            collection=final_doc.collection if final_doc else collection,
            format=doc.format.value, size_bytes=doc.size_bytes,
            chunk_count=len(chunks),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            status="ingested", document_id=document_id, curation=curation,
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
    auto_curate: bool = False,
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

    # 维度预检：换嵌入模型后未重建索引时，提前给出可操作的错误指引，
    # 而不是等写库时报一句没头没尾的"写库失败"。
    store_dim = getattr(store, "embedding_dim", None)
    embed_dim = getattr(embedder, "dimension", None)
    if store_dim is not None and embed_dim is not None and embed_dim != store_dim:
        return _fail(
            path, collection,
            f"嵌入模型维度 ({embed_dim}) 与向量库维度 ({store_dim}) 不一致："
            "请先在设置页执行「重建索引」（reindex），或切回原嵌入模型后再导入",
            t0,
        )

    # 写库：单事务原子替换（删旧 → 写文档 → 写分块），失败整体回滚。
    # UNIQUE(collection, source) 的"替换"语义由此实现；source 现为完整路径，
    # 不同目录的同名文件互不覆盖。
    document_id = uuid.uuid4().hex
    now = _now_iso()
    try:
        store.replace_document(
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
            ),
            chunks=chunks,
            embeddings=embeddings,
        )
    except Exception as e:  # noqa: BLE001
        return _fail(path, collection, f"写库失败: {e}", t0)

    # 入库自动整理（文件摄入只打标签/摘要，不自动归类——集合由调用方指定）
    curation = None
    if auto_curate:
        curation = _auto_curate_after_ingest(
            store, settings, document_id, allow_categorize=False
        )

    return IngestResult(
        source=doc.source, collection=collection,
        format=doc.format.value, size_bytes=doc.size_bytes,
        chunk_count=len(chunks),
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
        status="ingested", document_id=document_id, curation=curation,
    )


def _auto_curate_after_ingest(
    store: VectorStore,
    settings: Settings,
    document_id: str,
    allow_categorize: bool,
) -> dict | None:
    """入库成功后的 AI 自动整理：打标签/生成摘要（可选自动归类）。

    任何失败（LLM 未配置 / 调用出错 / 配置不完整）都只记日志并返回 None，
    绝不影响入库结果。curator 与 pipeline 相互引用，这里延迟导入打破循环。
    """
    if not getattr(settings, "auto_curate_on_ingest", False):
        return None
    try:
        from doc2mind.core.llm.base import LLMError
        from doc2mind.core.llm.factory import get_llm_client

        try:
            llm = get_llm_client(settings)
        except LLMError as e:
            logger.info("auto curate 跳过（LLM 配置不可用）: %s", e)
            return None
        if llm is None:
            return None

        from doc2mind.core import curator

        doc = store.get_document_by_id(document_id)
        if doc is None:
            return None
        out: dict = {
            "enrich": curator.enrich_document(
                store, llm, doc, max_chars=settings.curate_max_chars, dry_run=False
            )
        }
        if allow_categorize:
            out["categorize"] = curator.categorize_document(store, llm, doc, dry_run=False)

        # 自动抽取图谱实体并关联
        try:
            from doc2mind.core.extractor import extract_and_store

            rep_text = curator._doc_representative_text(store, doc, max_chars=1800)
            if len(rep_text) >= 50:
                first_chunk = store.list_chunks_by_document(doc.id, limit=1)
                first_chunk_id = first_chunk[0].id if first_chunk else None
                out["extract"] = extract_and_store(
                    rep_text,
                    doc.collection,
                    llm,
                    doc_id=doc.id,
                    db_path=settings.db_path,
                    chunk_id=first_chunk_id,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("入库自动图谱抽取失败（不影响入库）: %s", e)

        return out
    except Exception as e:  # noqa: BLE001 — 整理失败绝不影响入库
        logger.warning("auto curate 失败（不影响入库）: %s", e)
        return None


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
