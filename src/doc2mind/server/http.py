"""FastAPI HTTP 服务（extras `server`）。

按 [`docs/api.md`](../../docs/api.md) 契约实现端点：
    GET    /v1/health
    POST   /v1/ingest
    POST   /v1/search
    GET    /v1/documents         (列表 + 分页)
    GET    /v1/documents/{id}
    DELETE /v1/documents/{id}
    GET    /v1/stats
    GET    /v1/quality
    POST   /v1/convert
    POST   /v1/reindex
    GET    /v1/jobs/{id}
    GET    /v1/events            (SSE，可选)

启动：
    doc2mind serve
    uvicorn doc2mind.server.http:create_app --factory
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doc2mind.core.config import get_settings
from doc2mind.core.converter import (
    SUPPORTED_FORMATS,
    ConversionError,
    convert_document,
)
from doc2mind.core.embedder import get_embedder
from doc2mind.core.loader.detect import get_loader, is_supported
from doc2mind.core.pipeline import ingest_path, ingest_text
from doc2mind.core.retriever.search import Retriever
from doc2mind.core.store.sqlite_vec import VectorStore


# --- Pydantic 模型（请求 / 响应）---
try:
    from pydantic import BaseModel, Field
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "FastAPI 依赖未安装。请运行：pip install doc2mind[server]"
    ) from e


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


# --- 请求体 ---
class IngestRequest(BaseModel):
    path: str
    # 前端可能传 null（用户未填集合），容错为 Optional；空/None 时用 "default"
    collection: str | None = "default"
    recursive: bool = False
    force: bool = False

    model_config = {"populate_by_name": True}


class IngestTextRequest(BaseModel):
    """文本直入：AI 沉淀经验/笔记用，不依赖文件路径。"""
    text: str
    title: str | None = None
    collection: str | None = "default"
    force: bool = False

    model_config = {"populate_by_name": True}


class SearchRequest(BaseModel):
    query: str
    # 前端可能传 null（用户未填集合，表示跨全部集合），容错为 Optional
    collection: str | None = None
    top_k: int = Field(10, ge=1, le=100, validation_alias="topK")
    # 前端可能传 null（未设置最低分），容错为 Optional
    min_score: float | None = Field(None, ge=0.0, le=1.0, validation_alias="minScore")
    filter: dict[str, Any] | None = None
    highlight: bool = False

    model_config = {"populate_by_name": True}


class ConvertRequest(BaseModel):
    input_path: str = Field(validation_alias="inputPath")
    output_format: str = Field("md", validation_alias="format")
    output_path: str | None = Field(None, validation_alias="outputPath")

    model_config = {"populate_by_name": True}


class ReindexRequest(BaseModel):
    collection: str = "default"
    model: str | None = None
    # WPF 客户端发的 `full` 字段后端不用，但需接受以避免 422
    full: bool = False

    model_config = {"populate_by_name": True}


# 设置页可调的后端运行参数（嵌入 + 分块 + 检索）。
class ConfigUpdate(BaseModel):
    embed_model: str | None = None
    # 本地模型目录（DOC2MIND_EMBED_MODEL_PATH）；空字符串 = 清除本地模型
    embed_model_path: str | None = None
    embed_batch_size: int | None = None
    chunk_max_tokens: int | None = None
    chunk_min_chars: int | None = None
    chunk_overlap_chars: int | None = None
    chunk_max_chars: int | None = None
    search_top_k: int | None = None
    rrf_k: int | None = None

    model_config = {"populate_by_name": True}


# --- 响应体 ---
class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    uptime_seconds: int


class ConfigResponse(BaseModel):
    embed_model: str
    embed_batch_size: int
    chunk_max_tokens: int
    chunk_min_chars: int
    chunk_overlap_chars: int
    chunk_max_chars: int
    search_top_k: int
    rrf_k: int
    # 可选提示（如切换模型后需要重建索引）；null 表示无提示
    notice: str | None = None


class DocumentDTO(BaseModel):
    id: str
    source: str
    collection: str
    format: str
    file_hash: str
    size_bytes: int
    page_count: int | None
    chunk_count: int
    created_at: str
    updated_at: str


class IngestResultDTO(BaseModel):
    source: str
    collection: str
    format: str
    size_bytes: int
    chunk_count: int
    elapsed_ms: int
    status: str
    error: str | None = None
    document_id: str | None = None


class IngestResponse(BaseModel):
    ingested: list[IngestResultDTO]
    skipped: int
    failed: int
    # 失败明细：每个失败文件的 source + error（status="failed" 的 IngestResultDTO），
    # 前端据此展示"哪个文件为什么失败"，而不是只显示计数。
    failed_details: list[IngestResultDTO] = []
    total_documents: int
    total_chunks: int


class SearchHitDTO(BaseModel):
    rank: int
    score: float
    match_type: str
    vector_score: float
    bm25_score: float
    source: str
    format: str
    page: int | None
    heading: str | None
    content: str


class SearchResponse(BaseModel):
    query: str
    total: int
    elapsed_ms: int
    hits: list[SearchHitDTO]


class ListDocumentsResponse(BaseModel):
    documents: list[DocumentDTO]
    total: int
    page: int
    page_size: int


class StatsResponse(BaseModel):
    total_documents: int
    total_chunks: int
    collections: dict[str, list[int]]


class QualityResponse(BaseModel):
    collection: str | None
    total_documents: int
    total_chunks: int
    format_distribution: dict[str, int]
    warnings: list[str] = []


class ConvertResponse(BaseModel):
    input: str
    output_format: str
    content: str
    elements_count: int


class DeleteResponse(BaseModel):
    id: str
    deleted_chunks: int
    status: str = "deleted"


class JobStatus(BaseModel):
    job_id: str
    type: str
    status: str
    progress: float = 0.0
    processed: int = 0
    total: int = 0
    started_at: str
    finished_at: str | None = None
    error: str | None = None


class ApiError(BaseModel):
    code: str
    message: str
    detail: Any | None = None


# --- 全局状态（单进程内的 store / embedder）---
class _AppState:
    """每个 FastAPI app 实例的共享状态。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedder = None
        self.store: VectorStore | None = None
        self.jobs: dict[str, JobStatus] = {}
        self.started_at = datetime.now(timezone.utc)
        # 同步锁：ensure_open 是同步方法，并发首次请求需互斥创建 store/embedder
        self._lock = threading.Lock()
        # jobs 由 reindex 后台线程写、GET /v1/jobs 事件循环线程读，需独立锁
        self._jobs_lock = threading.Lock()

    def ensure_open(self) -> VectorStore:
        # 双检锁：避免并发首次请求重复创建 store/embedder
        if self.store is not None and self.embedder is not None:
            return self.store
        with self._lock:
            if self.embedder is None:
                self.embedder = get_embedder(self.settings)
            if self.store is None:
                self.store = VectorStore(
                    self.settings.db_path, self.embedder.dimension
                )
                self.store.open()
            return self.store


def create_app() -> Any:
    """创建 FastAPI app 实例。

    用 factory 模式便于测试隔离与 uvicorn 启动。
    """
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.responses import StreamingResponse
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "FastAPI 依赖未安装。请运行：pip install doc2mind[server]"
        ) from e

    app = FastAPI(
        title="DocMind",
        description="轻量向量知识库 HTTP API",
        version="0.1.0",
    )
    state = _AppState()
    app.state.doc2mind = state

    # --- GET /v1/health ---
    @app.get("/v1/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        uptime = int((datetime.now(timezone.utc) - state.started_at).total_seconds())
        return HealthResponse(
            version="0.1.0",
            uptime_seconds=uptime,
        )

    # --- GET/POST /v1/config（设置页：嵌入模型 + 分块 + 检索参数）---
    @app.get("/v1/config", response_model=ConfigResponse)
    async def get_config() -> ConfigResponse:
        s = state.settings
        return ConfigResponse(
            embed_model=s.embed_model,
            embed_batch_size=s.embed_batch_size,
            chunk_max_tokens=s.chunk_max_tokens,
            chunk_min_chars=s.chunk_min_chars,
            chunk_overlap_chars=s.chunk_overlap_chars,
            chunk_max_chars=s.chunk_max_chars,
            search_top_k=s.search_top_k,
            rrf_k=s.rrf_k,
        )

    @app.post("/v1/config", response_model=ConfigResponse)
    async def update_config(req: ConfigUpdate) -> ConfigResponse:
        s = state.settings
        old_model = s.embed_model
        # 允许修改的字段 → 更新运行时 settings（后续导入/检索生效）
        updates = req.model_dump(exclude_none=True)
        if updates:
            for k, v in updates.items():
                if hasattr(s, k):
                    setattr(s, k, v)

        # 模型切换引导：维度变化时提示用户重建索引
        notice: str | None = None
        if req.embed_model and req.embed_model != old_model:
            from doc2mind.core.embedder.catalog import get_model_info

            new_info = get_model_info(req.embed_model)
            old_info = get_model_info(old_model)
            new_dim = new_info.dim if new_info else None
            old_dim = old_info.dim if old_info else None
            if new_dim is not None and old_dim is not None and new_dim != old_dim:
                notice = (
                    f"嵌入模型维度由 {old_dim} 变为 {new_dim}，"
                    "请对已有集合执行「重建索引」后检索才生效（设置页 → 文档管理）。"
                )

        # 持久化到 config.toml（下次启动自动生效）
        try:
            from doc2mind.core.config import save_settings

            save_settings(s)
        except Exception:  # noqa: BLE001 — 持久化失败不影响本次更新
            pass

        return ConfigResponse(
            embed_model=s.embed_model,
            embed_batch_size=s.embed_batch_size,
            chunk_max_tokens=s.chunk_max_tokens,
            chunk_min_chars=s.chunk_min_chars,
            chunk_overlap_chars=s.chunk_overlap_chars,
            chunk_max_chars=s.chunk_max_chars,
            search_top_k=s.search_top_k,
            rrf_k=s.rrf_k,
            notice=notice,
        )

    # --- POST /v1/ingest ---
    @app.post("/v1/ingest", response_model=IngestResponse)
    async def ingest(req: IngestRequest) -> IngestResponse:
        p = Path(req.path)
        if not p.exists():
            raise _api_error("BAD_REQUEST", f"路径不存在: {req.path}", 400)

        # collection 容错：None/空 → "default"
        collection = req.collection.strip() if req.collection else "default"
        if not collection:
            collection = "default"

        # 复用 _AppState 的单例 store，避免每次请求新建 sqlite 连接
        # 触发 WAL 锁冲突。嵌入是 CPU 密集，用线程避免阻塞事件循环。
        store = state.ensure_open()
        summary = await asyncio.to_thread(
            ingest_path,
            path=p,
            collection=collection,
            recursive=req.recursive,
            force=req.force,
            store=store,
        )
        return IngestResponse(
            ingested=[
                IngestResultDTO(**r.__dict__) for r in summary.results
                if r.status == "ingested"
            ],
            skipped=summary.skipped,
            failed=summary.failed,
            total_documents=summary.total_documents,
            total_chunks=summary.total_chunks,
            failed_details=[
                IngestResultDTO(**r.__dict__) for r in summary.results
                if r.status == "failed"
            ],
        )

    # --- POST /v1/ingest/text（文本直入：AI 沉淀经验/笔记） ---
    @app.post("/v1/ingest/text", response_model=IngestResponse)
    async def ingest_text_endpoint(req: IngestTextRequest) -> IngestResponse:
        if not req.text or not req.text.strip():
            raise _api_error("BAD_REQUEST", "text 不能为空", 400)

        collection = req.collection.strip() if req.collection else "default"
        if not collection:
            collection = "default"

        store = state.ensure_open()
        result = await asyncio.to_thread(
            ingest_text,
            text=req.text,
            title=req.title or "",
            collection=collection,
            force=req.force,
            store=store,
        )
        ingested = [IngestResultDTO(**result.__dict__)] if result.status == "ingested" else []
        failed_details = [IngestResultDTO(**result.__dict__)] if result.status == "failed" else []
        return IngestResponse(
            ingested=ingested,
            skipped=1 if result.status == "skipped" else 0,
            failed=1 if result.status == "failed" else 0,
            total_documents=1 if result.status == "ingested" else 0,
            total_chunks=result.chunk_count if result.status == "ingested" else 0,
            failed_details=failed_details,
        )

    # --- POST /v1/ingest/job（异步摄入：大目录不阻塞请求，返回 job_id 轮询进度） ---
    @app.post("/v1/ingest/job", response_model=JobStatus)
    async def ingest_job(req: IngestRequest) -> JobStatus:
        p = Path(req.path)
        if not p.exists():
            raise _api_error("BAD_REQUEST", f"路径不存在: {req.path}", 400)

        collection = req.collection.strip() if req.collection else "default"
        if not collection:
            collection = "default"

        store = state.ensure_open()
        job_id = _new_id()
        job = JobStatus(
            job_id=job_id,
            type="ingest",
            status="running",
            progress=0.0,
            processed=0,
            total=0,
            started_at=_now_iso(),
        )
        with state._jobs_lock:
            state.jobs[job_id] = job

        def _run_ingest_job() -> None:
            try:
                summary = ingest_path(
                    path=p,
                    collection=collection,
                    recursive=req.recursive,
                    force=req.force,
                    store=store,
                    progress=lambda done, total: _update_ingest_job(state, job, done, total),
                )
                with state._jobs_lock:
                    job.status = "completed"
                    job.progress = 1.0
                    job.processed = summary.total_documents + summary.skipped + summary.failed
                    job.finished_at = _now_iso()
            except Exception as e:  # noqa: BLE001
                with state._jobs_lock:
                    job.status = "failed"
                    job.error = str(e)
                    job.finished_at = _now_iso()

        threading.Thread(target=_run_ingest_job, daemon=True).start()
        return job

    # --- POST /v1/search ---
    @app.post("/v1/search", response_model=SearchResponse)
    async def search(req: SearchRequest) -> SearchResponse:
        store = state.ensure_open()
        try:
            retriever = Retriever(store=store, embedder=state.embedder)
            hits, stats = await asyncio.to_thread(
                retriever.search,
                req.query,
                req.collection,  # None = 跨全部集合
                req.top_k,
                req.min_score or 0.0,  # Optional → float
            )
        except Exception as e:  # noqa: BLE001
            raise _api_error("INTERNAL", f"检索失败: {e}", 500) from e

        return SearchResponse(
            query=req.query,
            total=len(hits),
            elapsed_ms=stats.elapsed_ms,
            hits=[
                SearchHitDTO(
                    rank=h.rank,
                    score=round(h.score, 4),
                    match_type=h.match_type,
                    vector_score=round(h.vector_score, 4),
                    bm25_score=round(h.bm25_score, 4),
                    source=h.chunk.source,
                    format=h.chunk.format,
                    page=h.chunk.page,
                    heading=h.chunk.heading,
                    content=h.chunk.content,
                )
                for h in hits
            ],
        )

    # --- GET /v1/documents ---
    @app.get("/v1/documents", response_model=ListDocumentsResponse)
    async def list_documents(
        collection: str | None = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
        format: str | None = Query(None),
        sort: str = Query("created_at_desc"),
    ) -> ListDocumentsResponse:
        store = state.ensure_open()
        offset = (page - 1) * page_size
        docs = store.list_documents(
            collection=collection,
            limit=page_size,
            offset=offset,
            format=format,
            sort=sort,
        )
        total = store.count_documents(collection, format)
        return ListDocumentsResponse(
            documents=[
                DocumentDTO(
                    id=d.id, source=d.source, collection=d.collection,
                    format=d.format, file_hash=d.file_hash,
                    size_bytes=d.size_bytes, page_count=d.page_count,
                    chunk_count=d.chunk_count, created_at=d.created_at,
                    updated_at=d.updated_at,
                )
                for d in docs
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    # --- GET /v1/documents/{id} ---
    @app.get("/v1/documents/{doc_id}")
    async def get_document(
        doc_id: str,
        chunks: int = Query(5, ge=0, le=100),
        chunk_content_length: int = Query(200, ge=0, le=2000, alias="chunkContentLength"),
        collection: str | None = Query(None),
    ) -> dict:
        store = state.ensure_open()
        match = store.get_document_by_id(doc_id)
        if match is None:
            raise _api_error("NOT_FOUND", f"文档不存在: {doc_id}", 404)

        # 真实 chunks 预览：按 chunk_index 顺序取前 N 条，内容截断
        preview = []
        if chunks > 0:
            stored = store.list_chunks_by_document(doc_id, limit=chunks)
            for c in stored:
                content = c.content
                if chunk_content_length and len(content) > chunk_content_length:
                    content = content[:chunk_content_length] + "…"
                preview.append(
                    {
                        "chunk_id": c.id,
                        "chunk_index": c.chunk_index,
                        "content": content,
                        "tokens": c.tokens,
                        "doc_type": c.doc_type,
                        "page": c.page,
                        "heading": c.heading,
                    }
                )
        return {
            "document": match.__dict__,
            "chunks_preview": preview,
        }

    # --- DELETE /v1/documents/{id} ---
    @app.delete("/v1/documents/{doc_id}", response_model=DeleteResponse)
    async def delete_document(doc_id: str) -> DeleteResponse:
        store = state.ensure_open()
        n = store.delete_document(doc_id)
        if n < 0:
            raise _api_error("NOT_FOUND", f"文档不存在: {doc_id}", 404)
        return DeleteResponse(id=doc_id, deleted_chunks=n)

    # --- GET /v1/stats ---
    @app.get("/v1/stats", response_model=StatsResponse)
    async def stats(collection: str | None = Query(None)) -> StatsResponse:
        store = state.ensure_open()
        # collection 过滤：只统计该集合；None 为全部
        if collection:
            docs = store.list_documents(collection=collection, limit=100000)
            total_documents = len(docs)
            total_chunks = sum(d.chunk_count for d in docs)
            total_size = sum(d.size_bytes for d in docs)
            collections_dict = {collection: [total_documents, total_chunks, total_size]}
        else:
            s = store.get_stats()
            total_documents = s.total_documents
            total_chunks = s.total_chunks
            collections_dict = {
                name: [dc, cc, sz] for name, (dc, cc, sz) in s.collections.items()
            }
        return StatsResponse(
            total_documents=total_documents,
            total_chunks=total_chunks,
            collections=collections_dict,
        )

    # --- GET /v1/quality ---
    @app.get("/v1/quality", response_model=QualityResponse)
    async def quality(collection: str | None = Query(None)) -> QualityResponse:
        store = state.ensure_open()
        docs = store.list_documents(collection=collection, limit=10000)
        fmt_dist: dict[str, int] = {}
        total_chunks = 0
        for d in docs:
            fmt_dist[d.format] = fmt_dist.get(d.format, 0) + 1
            total_chunks += d.chunk_count

        # 质量告警：从真实库内数据计算，供 UI 警告面板展示
        warnings: list[str] = []
        for d in docs:
            if d.chunk_count == 0:
                warnings.append(
                    f"文档「{d.source}」分块数为 0，可能无法被检索到"
                )
            if d.size_bytes > 50 * 1024 * 1024:
                warnings.append(
                    f"文档「{d.source}」体积较大 "
                    f"({d.size_bytes / (1024 * 1024):.1f} MB)，建议拆分后导入"
                )
        # 空分块文档过多时避免刷屏，保留前 20 条
        warnings = warnings[:20]
        if not warnings and docs:
            warnings.append("未发现质量问题")

        return QualityResponse(
            collection=collection,
            total_documents=len(docs),
            total_chunks=total_chunks,
            format_distribution=fmt_dist,
            warnings=warnings,
        )

    # --- POST /v1/convert ---
    @app.post("/v1/convert", response_model=ConvertResponse)
    async def convert(req: ConvertRequest) -> ConvertResponse:
        if req.output_format not in SUPPORTED_FORMATS:
            raise _api_error(
                "BAD_REQUEST",
                f"不支持的格式: {req.output_format}",
                400,
            )
        p = Path(req.input_path)
        if not p.exists():
            raise _api_error("BAD_REQUEST", f"文件不存在: {req.input_path}", 400)
        if not is_supported(p):
            raise _api_error("UNSUPPORTED_FORMAT", f"不支持的文件格式: {p.suffix}", 415)

        # 同步的 loader.extract + convert_document 移到线程池，
        # 避免大 PDF 解析阻塞事件循环导致前端 HttpClient 超时误判"失败"
        def _do_convert() -> tuple["LoadedDocument", str]:
            try:
                loader = get_loader(p)
                doc = loader.extract(p)
                content = convert_document(doc, req.output_format)
                return doc, content
            except ConversionError as e:
                raise _api_error("INTERNAL", str(e), 500) from e
            except Exception as e:  # noqa: BLE001
                raise _api_error("INTERNAL", f"加载失败: {e}", 500) from e

        doc, content = await asyncio.to_thread(_do_convert)

        # 写文件（如果指定 output_path）
        if req.output_path:
            try:
                Path(req.output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(req.output_path).write_text(content, encoding="utf-8")
            except OSError as e:  # noqa: BLE001
                raise _api_error(
                    "INTERNAL", f"写输出文件失败: {e}", 500
                ) from e

        return ConvertResponse(
            input=p.name,
            output_format=req.output_format,
            content=content,
            elements_count=len(doc.elements),
        )

    # --- POST /v1/reindex ---
    @app.post("/v1/reindex", response_model=JobStatus)
    async def reindex(req: ReindexRequest) -> JobStatus:
        # 真实实现：后台线程重新嵌入 collection 内已有 chunks（任务化）。
        # req.model 非空 → 用目标模型重嵌入（换模型重建索引）：
        #   先创建目标 embedder 探测维度；与现有库维度不一致时不拒绝，
        #   由 _run_reindex 重建向量表（drop + 按新维度建表 + 回填全部向量）。
        # 注意：store 可能是跨线程共享单例，embedder 在 ensure_open 中创建，
        # 因此这里只持有引用，不关闭。
        store = state.ensure_open()
        collection = (req.collection or "default").strip() or "default"
        job_id = _new_id()

        # 目标嵌入器：默认复用当前；指定 model 时用临时 settings 创建并探测维度
        target_embedder = state.embedder
        need_rebuild = False
        if req.model and req.model.strip():
            model_name = req.model.strip()
            try:
                from dataclasses import replace

                target_settings = replace(state.settings, embed_model=model_name)
                candidate = get_embedder(target_settings)
                # 真实加载目标模型并探测维度：dimension 属性在模型加载前只返回
                # settings 预设值（默认 512），直接拿它判断 need_rebuild 会在
                # 换维度模型（如 bge-base=768）时误判为"维度相同"，把新维度向量
                # 写进旧维度表导致数据损坏。用一次 probe 嵌入强制触发加载。
                _ = list(candidate.embed_texts(["probe"]))
            except Exception as e:  # noqa: BLE001
                raise _api_error(
                    "BAD_REQUEST", f"加载嵌入模型失败: {model_name}: {e}", 400
                ) from e
            if candidate.dimension != store.embedding_dim:
                # 维度变化：不拒绝，改为重建向量表（在 _run_reindex 中执行）
                need_rebuild = True
            target_embedder = candidate

        job = JobStatus(
            job_id=job_id,
            type="reindex",
            status="running",
            progress=0.0,
            processed=0,
            total=0,
            started_at=_now_iso(),
        )
        with state._jobs_lock:
            state.jobs[job_id] = job

        def _run_reindex() -> None:
            try:
                pairs = store.list_chunk_contents(collection)
                total = len(pairs)
                with state._jobs_lock:
                    job.total = total
                if total == 0:
                    with state._jobs_lock:
                        job.status = "completed"
                        job.progress = 1.0
                        job.finished_at = _now_iso()
                    return

                embedder = target_embedder
                if embedder is None:
                    raise RuntimeError("嵌入器未初始化")
                # 分批重新嵌入，逐批更新向量，避免一次加载全部结果。
                # need_rebuild（维度变化）时不能原地 update_embeddings（表结构还是旧维度），
                # 先收集全部 (chunk_id, embedding)，最后一次性重建向量表并回填。
                BATCH = 32
                processed = 0
                rebuild_pairs: list[tuple[int, object]] = []
                for i in range(0, total, BATCH):
                    batch = pairs[i : i + BATCH]
                    texts = [content for _, content in batch]
                    embeddings = list(embedder.embed_texts(texts))
                    if len(embeddings) != len(batch):
                        raise RuntimeError(
                            f"嵌入数量 ({len(embeddings)}) 与批次 ({len(batch)}) 不一致"
                        )
                    new_pairs = [
                        (cid, emb) for (cid, _), emb in zip(batch, embeddings)
                    ]
                    if need_rebuild:
                        rebuild_pairs.extend(new_pairs)
                    else:
                        store.update_embeddings(new_pairs)
                    processed += len(batch)
                    with state._jobs_lock:
                        job.processed = processed
                        job.progress = round(processed / total, 4)

                if need_rebuild:
                    # 维度变化：重建向量表（drop + 按新维度建表）并回填全部向量
                    store.rebuild_chunk_embeddings(rebuild_pairs, embedder.dimension)

                with state._jobs_lock:
                    job.status = "completed"
                    job.progress = 1.0
                    job.finished_at = _now_iso()

                # 换模型重建成功后，把全局 embedder 切换为目标模型，
                # 使后续搜索 / 导入也使用新模型。
                if target_embedder is not state.embedder:
                    state.embedder = target_embedder
            except Exception as e:  # noqa: BLE001
                with state._jobs_lock:
                    job.status = "failed"
                    job.error = str(e)
                    job.finished_at = _now_iso()

        threading.Thread(target=_run_reindex, daemon=True).start()
        return job

    # --- GET /v1/jobs/{id} ---
    @app.get("/v1/jobs/{job_id}", response_model=JobStatus)
    async def get_job(job_id: str) -> JobStatus:
        with state._jobs_lock:
            job = state.jobs.get(job_id)
        if job is None:
            raise _api_error("NOT_FOUND", f"任务不存在: {job_id}", 404)
        return job

    # --- GET /v1/events (SSE) ---
    @app.get("/v1/events")
    async def events() -> Any:
        """SSE 事件流（占位实现，每秒发心跳）。"""
        async def event_stream() -> Any:
            while True:
                payload = json.dumps(
                    {"type": "heartbeat", "ts": _now_iso()},
                    ensure_ascii=False,
                )
                yield f"data: {payload}\n\n"
                await asyncio.sleep(1.0)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # --- 启动/关闭钩子 ---
    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if state.store is not None:
            state.store.close()

    return app


# --- 辅助 ---
def _api_error(code: str, message: str, status: int) -> HTTPException:
    """构造统一错误响应。"""
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    return HTTPException(
        status_code=status,
        detail=ApiError(code=code, message=message).model_dump(),
    )


def _update_ingest_job(state: _AppState, job: JobStatus, done: int, total: int) -> None:
    """异步 ingest 的进度回调：更新 job.processed / progress（线程安全）。"""
    with state._jobs_lock:
        job.processed = done
        job.total = total
        job.progress = round(done / total, 4) if total > 0 else 0.0
