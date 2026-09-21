"""摄入流水线 — 把 loader→chunker→embedder→store 串起来。

入口：`ingest_path(path, settings, collection, force)` 返回 `IngestResult`
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doc2mind.core.chunker import chunk_document
from doc2mind.core.config import Settings, get_settings
from doc2mind.core.embedder import get_embedder
from doc2mind.core.loader.base import make_source, stream_file_hash
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


class IngestCancelled(Exception):
    """摄入任务取消信号。

    由进度回调（http 层的取消检查）注入，pipeline 各阶段原样向上传播、
    绝不吞掉转成 failed 结果；http/mcp 层捕获后把 job 标记为 cancelled。
    """


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
    # 导入健康：估算 token 超过 embed_max_length 的分块数（嵌入会截断）
    long_chunk_count: int = 0
    # 导入健康提示（人话，给导入完成卡片）
    health_warnings: tuple[str, ...] = ()
    # 建议试问的短句（取自文档标题/首段），供「试问一句」入口
    suggest_query: str | None = None


@dataclass
class IngestSummary:
    """批量摄入汇总。"""

    results: list[IngestResult] = field(default_factory=list)
    total_documents: int = 0
    total_chunks: int = 0
    skipped: int = 0
    failed: int = 0
    # 本次摄入成功入库、且满足自动整理护栏的文档 id。auto-curate 已从
    # 导入热路径剥离：调用方（http/mcp/file_watcher）拿这批 id 在导入
    # 结束后交给 run_background_curate 后台执行，导入速度不再受 LLM
    # 串行调用拖累。
    curatable_document_ids: list[str] = field(default_factory=list)
    # 导入健康汇总（整批）
    long_chunk_count: int = 0
    health_warnings: tuple[str, ...] = ()
    # 第一条可试问的建议查询（来自成功摄入的文档）
    suggest_query: str | None = None


def _estimate_chunk_tokens(text: str) -> int:
    """与 semantic chunker 同启发式估算 token（CJK≈1，其他≈4字符）。"""
    if not text:
        return 0
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _chunk_health(
    chunks: list,
    settings: Settings,
) -> tuple[int, list[str], str | None]:
    """计算分块健康信息：(超窗块数, 人话警告, 建议试问短句)。"""
    embed_max = int(getattr(settings, "embed_max_length", 512) or 512)
    long_count = 0
    for ch in chunks:
        content = getattr(ch, "content", "") or ""
        if _estimate_chunk_tokens(content) > embed_max:
            long_count += 1

    warnings: list[str] = []
    if long_count:
        warnings.append(
            f"{long_count} 个分块超出嵌入窗口 {embed_max} token，检索时可能只命中前半截；"
            "建议调小 chunk_max_tokens 或换长窗口嵌入模型后重建索引"
        )
    if int(getattr(settings, "chunk_max_tokens", 0) or 0) > embed_max:
        warnings.append(
            f"当前配置 chunk_max_tokens={settings.chunk_max_tokens} 大于 "
            f"embed_max_length={embed_max}，新导入仍可能截断"
        )

    suggest = None
    if chunks:
        first = getattr(chunks[0], "content", "") or ""
        # 试问：取第一行或前 24 字
        line = first.strip().splitlines()[0].strip() if first.strip() else ""
        line = line.lstrip("#*·- ").strip()
        if len(line) > 24:
            line = line[:24]
        if line:
            suggest = line
    return long_count, warnings, suggest


def _aggregate_health(summary: IngestSummary) -> None:
    """把单文档健康信息汇总到 IngestSummary。"""
    long_total = 0
    warn_set: list[str] = []
    for r in summary.results:
        if r.status not in ("ingested", "updated"):
            continue
        long_total += r.long_chunk_count
        for w in r.health_warnings:
            if w not in warn_set:
                warn_set.append(w)
        if summary.suggest_query is None and r.suggest_query:
            summary.suggest_query = r.suggest_query
    summary.long_chunk_count = long_total
    summary.health_warnings = tuple(warn_set)


# --- 文件内阶段进度 ---

# 单文件（尤其大 PDF）导入时 done 长时间不变，前端进度条会停在
# done/total 上看起来像卡死。进度回调因此带上文件内阶段：
# parsing → chunking → embedding → writing → curating。
# embedding 阶段按 chunk 批次有真实 stage_progress（0~1），其余阶段
# 不可测（时长占比因文件类型差异极大），只报阶段名。
# 消费方用固定跨度把阶段折算成文件内整体进度（感知美化：让条动起来，
# 不承诺真实耗时比例），跨度保持单调递增且 write 完成时恰为 1.0。
STAGE_SPANS: dict[str, tuple[float, float]] = {
    "parsing": (0.0, 0.5),
    "chunking": (0.5, 0.6),
    "embedding": (0.6, 0.9),
    "writing": (0.9, 0.95),
    "curating": (0.95, 1.0),
}


def stage_fraction(stage: str | None, stage_progress: float | None) -> float:
    """把 (stage, stage_progress) 折算成文件内整体进度 0.0~1.0。"""
    if stage is None:
        return 0.0
    start, end = STAGE_SPANS.get(stage, (0.0, 0.0))
    frac = stage_progress if stage_progress is not None else 0.0
    return start + (end - start) * min(max(frac, 0.0), 1.0)


def _make_stage_reporter(
    progress: Callable[..., None], done: int, total: int, current_file: str
) -> Callable[[str, float | None], None]:
    """把文件内阶段上报接到底层 progress 回调。

    兼容只接受 (done, total) 的旧回调：带 stage 关键字调用抛
    TypeError 时降级为两参调用。
    """

    def report(stage: str, stage_progress: float | None = None) -> None:
        try:
            progress(done, total, current_file, stage=stage, stage_progress=stage_progress)
        except TypeError:
            progress(done, total)

    return report


def ingest_path(
    path: Path,
    settings: Settings | None = None,
    collection: str = "default",
    recursive: bool = False,
    force: bool = False,
    store: VectorStore | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> IngestSummary:
    """摄入一个文件或目录。

    Args:
        path: 文件或目录
        settings: 配置
        collection: 集合名
        recursive: 目录是否递归
        force: 即使 file_hash 已存在也重新摄入
        store: 已打开的 VectorStore；None 则内部创建并关闭
        progress: 进度回调 (done, total[, current_file, stage, stage_progress])，
            每处理完一个文件调用一次；文件处理中还会带 stage 上报文件内阶段。
            None 表示不回调
        cancel_event: 可选取消事件；线程间共享，某线程 set() 后 ingest 在
            下一个文件级/批次级检查点抛出 IngestCancelled。

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

    # --- 摄入护栏：单文件大小 / 单次数量（防病态输入整读打爆内存） ---
    dropped_files: list[tuple[Path, str]] = []
    size_limit_bytes = int(getattr(settings, "max_file_size_mb", 0) or 0) * 1024 * 1024
    count_limit = int(getattr(settings, "max_files_per_import", 0) or 0)
    if size_limit_bytes > 0:
        kept: list[Path] = []
        for f in files:
            try:
                too_big = f.stat().st_size > size_limit_bytes
            except OSError:
                too_big = False  # stat 失败（权限等）不拦截，交给 loader 报真实错误
            if too_big:
                dropped_files.append(
                    (f, f"超过单文件大小上限 {settings.max_file_size_mb}MB，已跳过")
                )
            else:
                kept.append(f)
        files = kept
    if count_limit > 0 and len(files) > count_limit:
        for f in files[count_limit:]:
            dropped_files.append((f, f"超过单次导入数量上限 {count_limit}，已丢弃"))
        files = files[:count_limit]
    if dropped_files:
        logger.warning("导入护栏: 丢弃 %d 个超限文件", len(dropped_files))

    if not files:
        # 全部被护栏拦截：直接返回，避免白白加载嵌入模型
        summary = IngestSummary()
        _record_dropped(summary, dropped_files, collection)
        logger.info("ingest 完成(无文件可处理): 路径=%s 超限丢弃=%d", path, len(dropped_files))
        return summary

    embedder = get_embedder(settings)
    owns_store = store is None
    if store is None:
        store = VectorStore(
            settings.db_path, embedder.dimension,
            bm25_jieba_enabled=settings.bm25_jieba_enabled,
            sparse_retrieval_enabled=settings.sparse_retrieval_enabled,
        )
        store.open()

    total = len(files)
    # 立即上报一次 (0, total)：让前端马上拿到文件总数，而不是等第一个文件
    # 处理完才知道（单大文件导入时表现为长时间 0/0）。
    if progress is not None:
        progress(0, total)
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
    if dropped_files:
        _record_dropped(summary, dropped_files, collection)
    try:
        workers = max(1, int(getattr(settings, "ingest_workers", 1) or 1))
        if workers > 1 and len(files) > 1:
            _ingest_parallel(
                files, settings, collection, force, store, embedder,
                progress, summary, workers, cancel_event,
            )
        else:
            for idx, f in enumerate(files, start=1):
                # 文件级取消检查点：在处理每个文件前检查
                if cancel_event is not None and cancel_event.is_set():
                    raise IngestCancelled("任务已被取消")
                res = _ingest_one(
                    f, settings, collection, force, store, embedder,
                    report_stage=(
                        None if progress is None
                        else _make_stage_reporter(progress, idx - 1, total, f.name)
                    ),
                    cancel_event=cancel_event,
                )
                _record_result(summary, res)
                if progress is not None:
                    try:
                        progress(idx, total, f.name)
                    except TypeError:
                        progress(idx, total)
        # auto-curate 已剥离出热路径：只收集待整理文档 id，由调用方在导入
        # 结束后交给 run_background_curate 后台执行（含图谱抽取）。
        if auto_curate:
            summary.curatable_document_ids = [
                r.document_id for r in summary.results
                if r.status == "ingested" and r.document_id
            ]
        logger.info(
            "ingest 完成: 路径=%s collection=%s ingested=%d skipped=%d failed=%d 总文档=%d 总chunks=%d 后台待整理=%d",
            path, collection, summary.total_documents, summary.skipped,
            summary.failed, summary.total_documents, summary.total_chunks,
            len(summary.curatable_document_ids),
        )
        _aggregate_health(summary)
        return summary
    finally:
        if owns_store:
            store.close()


def _record_result(summary: IngestSummary, res: IngestResult) -> None:
    """把单文件结果累计进批量汇总。"""
    summary.results.append(res)
    if res.status == "ingested":
        summary.total_documents += 1
        summary.total_chunks += res.chunk_count
    elif res.status == "skipped":
        summary.skipped += 1
    elif res.status == "failed":
        summary.failed += 1


def _record_dropped(summary: IngestSummary, dropped: list[tuple[Path, str]], collection: str) -> None:
    """把护栏拦截的超限文件记为 skipped 结果（source 可见原因）。"""
    for f, why in dropped:
        _record_result(summary, IngestResult(
            source=str(f), collection=collection, format="unknown",
            size_bytes=0, chunk_count=0, elapsed_ms=0, status="skipped",
            error=why,
        ))


def _ingest_parallel(
    files: list[Path],
    settings: Settings,
    collection: str,
    force: bool,
    store: VectorStore,
    embedder,
    progress: Callable[[int, int], None] | None,
    summary: IngestSummary,
    workers: int,
    cancel_event: threading.Event | None = None,
) -> None:
    """两段式文件级并行（ingest_workers>1 时启用）。

    参考 LlamaIndex IngestionPipeline 的 num_workers 模式：
    - 第一段：worker 池并发执行 解析 → 去重 → 分块 → 嵌入（OCR/ONNX
      推理在 C 层释放 GIL，真正的并行收益在这里）；
    - 第二段：主线程按提交顺序逐个写库，SQLite 单写者语义与
      store 的事务边界完全不变。

    取消：进度回调抛出的 IngestCancelled 会在主线程取 future 结果时
    浮出，未启动的任务被撤销、在跑的自行收尾后整体向上传播。
    """
    total = len(files)
    pool = ThreadPoolExecutor(max_workers=workers)
    # 有界提交流水线：一批最多同时驻留 batch_size 份“文件+分块+向量”中间产物。
    # 历史上一次 submit 全部文件，海量小文件时 N 份完整中间产物同时占内存；
    # 改为按批提交、本批消费完再发下一批。
    batch_size = max(4, workers * 4)
    try:
        start = 0
        while start < total:
            chunk = files[start:start + batch_size]
            chunk_end = start + len(chunk)
            futures = []
            for idx, f in enumerate(chunk, start=start + 1):
                # 文件级取消检查点：在提交任务前检查
                if cancel_event is not None and cancel_event.is_set():
                    raise IngestCancelled("任务已被取消")
                reporter = (
                    None if progress is None
                    else _make_stage_reporter(progress, idx - 1, total, f.name)
                )
                futures.append(
                    pool.submit(_prepare_one, f, settings, collection, force,
                                store, embedder, reporter, cancel_event)
                )
            for idx, fut in zip(range(start + 1, chunk_end + 1), futures):
                try:
                    prep = fut.result()
                except IngestCancelled:
                    raise
                res = (
                    prep if isinstance(prep, IngestResult)
                    else _write_prepared(
                        store, prep,
                        report_stage=(
                            None if progress is None
                            else _make_stage_reporter(
                                progress, idx - 1, total, files[idx - 1].name
                            )
                        ),
                    )
                )
                _record_result(summary, res)
                if progress is not None:
                    try:
                        progress(idx, total, files[idx - 1].name)
                    except TypeError:
                        progress(idx, total)
            start = chunk_end
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


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
        store = VectorStore(
            settings.db_path, embedder.dimension,
            bm25_jieba_enabled=settings.bm25_jieba_enabled,
            sparse_retrieval_enabled=settings.sparse_retrieval_enabled,
        )
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
        long_count, health_warnings, suggest = _chunk_health(chunks, settings)
        return IngestResult(
            source=source,
            collection=final_doc.collection if final_doc else collection,
            format=doc.format.value, size_bytes=doc.size_bytes,
            chunk_count=len(chunks),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            status="ingested", document_id=document_id, curation=curation,
            long_chunk_count=long_count,
            health_warnings=tuple(health_warnings),
            suggest_query=suggest or display,
        )
    except Exception as e:  # noqa: BLE001
        return _fail(Path(source), collection, f"写库失败: {e}", t0)
    finally:
        if owns_store:
            store.close()


# loader.extract 是否支持 progress 形参（按类型探测一次并缓存，避免
# 每文件都 inspect，也避免靠 TypeError 探测引发二次解析）。
_EXTRACT_SUPPORTS_PROGRESS: dict[type, bool] = {}


def _extract_supports_progress(loader: object) -> bool:
    t = type(loader)
    flag = _EXTRACT_SUPPORTS_PROGRESS.get(t)
    if flag is None:
        try:
            flag = "progress" in inspect.signature(t.extract).parameters
        except (TypeError, ValueError):  # pragma: no cover — 内建/扩展类型
            flag = False
        _EXTRACT_SUPPORTS_PROGRESS[t] = flag
    return flag


@dataclass
class _Prepared:
    """单文件"准备完成待写库"的中间产物（解析+去重+分块+嵌入已就绪）。

    两段式并行时由 worker 池产出、主线程串行消费写库；顺序摄入时
    `_ingest_one` 内部串联两段，对外行为不变。
    """

    path: Path
    doc: LoadedDocument
    collection: str
    chunks: list
    embeddings: list
    embedder: Any
    document_id: str
    created_at: str
    t0: float
    settings: Settings | None = None


def _prepare_one(
    path: Path,
    settings: Settings,
    collection: str,
    force: bool,
    store: VectorStore,
    embedder,
    report_stage: Callable[[str, float | None], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> "_Prepared | IngestResult":
    """摄入单文件的准备段：解析 → 去重 → 分块 → 嵌入（不写库）。

    返回 `_Prepared`（就绪待写库）或 `IngestResult`（skipped / failed 终态）。
    线程安全：只读 store（去重查询）+ 共享 embedder（内部自带批次锁），
    可在 ingest_workers>1 的 worker 池中并发执行。

    report_stage: 文件内阶段上报 (stage, stage_progress)，供异步 job
    展示解析/切片/嵌入等子进度；None 表示不上报。
    cancel_event: 可选取消事件；线程间共享，某线程 set() 后 ingest 在
        下一个批次级检查点抛出 IngestCancelled。
    """
    t0 = time.perf_counter()

    def _emit(stage: str, stage_progress: float | None = None) -> None:
        if report_stage is not None:
            report_stage(stage, stage_progress)

    _emit("parsing")
    try:
        loader = get_loader(path)
    except IngestCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        return _fail(path, collection, str(e), t0)

    # 去重前移：解析前先流式算 MD5 查重，命中直接跳过 —— 重复导入
    # 不再支付解析/OCR/嵌入的全额成本（旧逻辑先解析再查重，扫描件
    # 重导一遍要白跑完整 OCR）。流式 MD5 与 loader 全量 md5 结果一致，
    # 入库仍写 doc.file_hash，与历史数据兼容。
    pre_size = 0
    if not force:
        try:
            pre_hash, pre_size = stream_file_hash(path)
        except OSError as e:
            return _fail(path, collection, f"读取文件失败: {e}", t0)
        existing = store.find_document_id_by_hash(pre_hash, collection)
        if existing is not None:
            logger.info(
                "ingest 跳过(已存在): source=%s collection=%s doc_id=%s",
                str(path), collection, existing,
            )
            return IngestResult(
                source=make_source(path), collection=collection,
                format=path.suffix.lstrip(".").lower() or "unknown",
                size_bytes=pre_size, chunk_count=0,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
                status="skipped", document_id=existing,
            )

    # 页级进度：PDF 逐页解析 / 扫描件逐页 OCR 时上报 (已完成页, 总页)，
    # 折算成 parsing 阶段进度；total<=0 表示总页数未知（不定进度）。
    def _page_cb(done: int, page_total: int) -> None:
        # 页级取消检查点：每页解析/OCR完成后检查
        if cancel_event is not None and cancel_event.is_set():
            raise IngestCancelled("任务已被取消")
        _emit("parsing", (done / page_total) if page_total > 0 else None)

    try:
        if _extract_supports_progress(loader):
            doc = loader.extract(path, progress=_page_cb)
        else:
            doc = loader.extract(path)
    except IngestCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        return _fail(path, collection, f"加载失败: {e}", t0)

    # 兜底去重（force=False 但文件在准备段被并发修改等极端场景：
    # loader 计算的 hash 与预检查不一致时以 loader 结果为准再查一次）
    if not force and doc.file_hash:
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

    _emit("chunking")
    try:
        chunks = chunk_document(doc, settings)
    except IngestCancelled:
        raise
    except ChunkerError as e:
        return _fail(path, collection, f"分块失败: {e}", t0)

    if not chunks:
        return _fail(path, collection, "无有效内容", t0)

    # 嵌入：逐批消费迭代器，每个批次完成即上报阶段进度（fastembed/API
    # 均为惰性迭代，批次在内部按 embed_batch_size 计算）；同时让取消检查
    # 能在批次间生效，而不必等整份文件嵌完。
    # 嵌入器可能逐个 yield 向量，也可能按批 yield 向量列表；用"元素是否
    # 带长度"区分（向量的元素是标量，批次的元素是向量）。
    _emit("embedding", 0.0)
    embeddings: list = []
    embedded = 0
    try:
        for item in embedder.embed(chunks):
            # 批次级取消检查点：每批次嵌入完成后检查
            if cancel_event is not None and cancel_event.is_set():
                raise IngestCancelled("任务已被取消")
            if (
                isinstance(item, (list, tuple))
                and item
                and hasattr(item[0], "__len__")
            ):
                embeddings.extend(item)
                embedded += len(item)
            else:
                embeddings.append(item)
                embedded += 1
            _emit("embedding", embedded / len(chunks))
    except IngestCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        return _fail(path, collection, f"嵌入失败: {e}", t0)

    if len(embeddings) != len(chunks):
        return _fail(
            path, collection,
            f"嵌入数量 ({len(embeddings)}) 与分块数量 ({len(chunks)}) 不一致",
            t0,
        )

    return _Prepared(
        path=path,
        doc=doc,
        collection=collection,
        chunks=chunks,
        embeddings=embeddings,
        embedder=embedder,
        document_id=uuid.uuid4().hex,
        created_at=_now_iso(),
        t0=t0,
        settings=settings,
    )


def _write_prepared(
    store: VectorStore,
    prep: _Prepared,
    report_stage: Callable[[str, float | None], None] | None = None,
) -> IngestResult:
    """把准备好的分块/向量写入向量库（写段，必须在单写者上下文执行）。

    单事务原子替换（删旧 → 写文档 → 写分块），失败整体回滚。
    UNIQUE(collection, source) 的"替换"语义由此实现；source 现为完整
    路径，不同目录的同名文件互不覆盖。
    """

    def _emit(stage: str, stage_progress: float | None = None) -> None:
        if report_stage is not None:
            report_stage(stage, stage_progress)

    # 维度预检：换嵌入模型后未重建索引时，提前给出可操作的错误指引，
    # 而不是等写库时报一句没头没尾的"写库失败"。
    store_dim = getattr(store, "embedding_dim", None)
    embed_dim = getattr(prep.embedder, "dimension", None)
    if store_dim is not None and embed_dim is not None and embed_dim != store_dim:
        return _fail(
            prep.path, prep.collection,
            f"嵌入模型维度 ({embed_dim}) 与向量库维度 ({store_dim}) 不一致："
            "请先在设置页执行「重建索引」（reindex），或切回原嵌入模型后再导入",
            prep.t0,
        )

    _emit("writing")
    try:
        store.replace_document(
            StoredDocument(
                id=prep.document_id,
                source=prep.doc.source,
                collection=prep.collection,
                format=prep.doc.format.value,
                file_hash=prep.doc.file_hash,
                size_bytes=prep.doc.size_bytes,
                page_count=prep.doc.page_count,
                chunk_count=len(prep.chunks),
                created_at=prep.created_at,
                updated_at=prep.created_at,
            ),
            chunks=prep.chunks,
            embeddings=prep.embeddings,
        )
    except Exception as e:  # noqa: BLE001
        return _fail(prep.path, prep.collection, f"写库失败: {e}", prep.t0)

    long_count, health_warnings, suggest = _chunk_health(
        prep.chunks, prep.settings or get_settings()
    )
    return IngestResult(
        source=prep.doc.source, collection=prep.collection,
        format=prep.doc.format.value, size_bytes=prep.doc.size_bytes,
        chunk_count=len(prep.chunks),
        elapsed_ms=int((time.perf_counter() - prep.t0) * 1000),
        status="ingested", document_id=prep.document_id,
        long_chunk_count=long_count,
        health_warnings=tuple(health_warnings),
        suggest_query=suggest,
    )


def _ingest_one(
    path: Path,
    settings: Settings,
    collection: str,
    force: bool,
    store: VectorStore,
    embedder,
    report_stage: Callable[[str, float | None], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> IngestResult:
    """摄入单个文件（顺序路径）：准备段 + 写库段串联。"""
    prep = _prepare_one(path, settings, collection, force, store, embedder,
                        report_stage=report_stage, cancel_event=cancel_event)
    if isinstance(prep, IngestResult):
        return prep
    return _write_prepared(store, prep, report_stage=report_stage)


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


# 后台整理默认互斥锁：调用方（http / mcp / file_watcher）未提供写锁时，
# 用它保证同一进程内后台整理不与其它写操作并发（SQLite 单写者）。
_BG_CURATE_LOCK = threading.Lock()


def run_background_curate(
    settings: Settings,
    document_ids: list[str],
    collection: str,
    store: VectorStore | None = None,
    write_lock: threading.Lock | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """对一批已入库文档执行 AI 自动整理（enrich + 图谱抽取，不自动归类）。

    导入热路径剥离 auto-curate 的执行端：http 层在导入 job 完成后用独立
    线程调用本函数并包装成 type="curate" 的后台 job 广播进度；mcp /
    file_watcher 直接后台调用。不传 store 时自开自关（避免与调用方的
    store 生命周期纠缠）；不传 write_lock 时使用模块级默认锁。

    Returns:
        {"total", "processed", "failed", "cancelled"} 汇总
    """
    owns_store = store is None
    if store is None:
        embedder = get_embedder(settings)
        store = VectorStore(
            settings.db_path, embedder.dimension,
            bm25_jieba_enabled=settings.bm25_jieba_enabled,
            sparse_retrieval_enabled=settings.sparse_retrieval_enabled,
        )
        store.open()

    lock = write_lock or _BG_CURATE_LOCK
    processed = 0
    failed = 0
    cancelled = False
    total = len(document_ids)
    try:
        for doc_id in document_ids:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            try:
                with lock:
                    _auto_curate_after_ingest(
                        store, settings, doc_id, allow_categorize=False
                    )
                processed += 1
            except Exception as e:  # noqa: BLE001 — 单文档失败不拖垮整批
                failed += 1
                logger.warning("后台整理单文档失败 doc_id=%s: %s", doc_id, e)
            if progress is not None:
                try:
                    progress(processed + failed, total)
                except TypeError:
                    progress(processed + failed, total)
    finally:
        if owns_store:
            store.close()
    logger.info(
        "后台整理完成: collection=%s total=%d processed=%d failed=%d cancelled=%s",
        collection, total, processed, failed, cancelled,
    )
    return {
        "total": total, "processed": processed,
        "failed": failed, "cancelled": cancelled,
    }


def _fail(path: Path, collection: str, err: str, t0: float) -> IngestResult:
    logger.error("ingest 失败: source=%s collection=%s 原因: %s", Path(path).name, collection, err)
    return IngestResult(
        source=Path(path).name, collection=collection, format="unknown",
        size_bytes=0, chunk_count=0,
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
        status="failed", error=err,
    )


def reindex_store(
    collection: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """对知识库分块进行重新嵌入与向量索引重建。

    支持原地更新向量与跨维度表结构重建（rebuild_chunk_embeddings）。
    """
    settings = settings or get_settings()
    t0 = time.perf_counter()

    if model:
        from doc2mind.core.embedder.catalog import get_model_info

        info = get_model_info(model)
        dim = info.dim if info else settings.embed_dim
        embedder = get_embedder(settings, model_name=model, dimension=dim)
    else:
        embedder = get_embedder(settings)

    store = VectorStore(
            settings.db_path, embedder.dimension,
            bm25_jieba_enabled=settings.bm25_jieba_enabled,
            sparse_retrieval_enabled=settings.sparse_retrieval_enabled,
        )
    store.open()
    try:
        current_dim = store.dimension
        need_rebuild = current_dim != embedder.dimension

        # 上下文检索（C2）：启用且文档已含摘要（enrich 生成）时，嵌入文本前拼接
        # [文档摘要]... 前缀，改善长文档/跨章节召回。清单库需在开启后手动 reindex
        # 才生效；无摘要的文档退化为原文（不拼前缀）。
        contextual = bool(getattr(settings, "contextual_retrieval", False))
        pairs = (
            store.list_chunk_contexts(collection)
            if contextual
            else store.list_chunk_contents(collection)
        )
        total = len(pairs)
        if total == 0:
            return {
                "ok": True,
                "total": 0,
                "processed": 0,
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "model": embedder.model_name,
                "dimension": embedder.dimension,
                "contextual": contextual,
            }

        batch_size = settings.embed_batch_size or 32
        processed = 0
        rebuild_pairs: list[tuple[int, object]] = []

        for i in range(0, total, batch_size):
            batch = pairs[i : i + batch_size]
            if contextual:
                texts = [_contextual_chunk_text(c, summary) for _, c, summary in batch]
            else:
                texts = [content for _, content in batch]
            embeddings = list(embedder.embed_texts(texts))
            if len(embeddings) != len(batch):
                raise RuntimeError(f"嵌入数量 ({len(embeddings)}) 与批次 ({len(batch)}) 不一致")

            new_pairs = [
                (cid, emb) for (cid, *_), emb in zip(batch, embeddings, strict=False)
            ]
            if need_rebuild:
                rebuild_pairs.extend(new_pairs)
            else:
                store.update_embeddings(new_pairs)

            processed += len(batch)
            if progress_callback:
                progress_callback(processed, total)

        if need_rebuild:
            store.rebuild_chunk_embeddings(rebuild_pairs, embedder.dimension)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "total": total,
            "processed": processed,
            "elapsed_ms": elapsed_ms,
            "model": embedder.model_name,
            "dimension": embedder.dimension,
            "rebuilt_table": need_rebuild,
            "contextual": contextual,
        }
    finally:
        store.close()


def _contextual_chunk_text(content: str, doc_summary: str | None) -> str:
    """上下文检索（C2）嵌入文本：在 chunk 正文前拼接文档摘要前缀。

    仅当文档已含摘要（enrich 生成）时拼接 `[文档摘要]<summary>`；无摘要退化
    为原文，绝不改变 chunk 语义或破坏检索/写入。返回的文本仅用于嵌入，
    不落库、不进入 chunks_meta（正文仍存原文）。
    """
    summary = (doc_summary or "").strip()
    if not summary:
        return content
    return f"[文档摘要]{summary}\n{content}"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

