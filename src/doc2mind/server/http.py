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
from doc2mind.core.pipeline import ingest_path
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
    collection: str = "default"
    recursive: bool = False
    force: bool = False


class SearchRequest(BaseModel):
    query: str
    collection: str = "default"
    top_k: int = Field(10, ge=1, le=100)
    min_score: float = Field(0.0, ge=0.0, le=1.0)
    filter: dict[str, Any] | None = None
    highlight: bool = False


class ConvertRequest(BaseModel):
    input_path: str
    output_format: str = "md"
    output_path: str | None = None


class ReindexRequest(BaseModel):
    collection: str = "default"
    model: str | None = None


# --- 响应体 ---
class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    uptime_seconds: int


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
        self._lock = asyncio.Lock()

    def ensure_open(self) -> VectorStore:
        if self.embedder is None:
            self.embedder = get_embedder(self.settings)
        if self.store is None:
            self.store = VectorStore(self.settings.db_path, self.embedder.dimension)
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

    # --- POST /v1/ingest ---
    @app.post("/v1/ingest", response_model=IngestResponse)
    async def ingest(req: IngestRequest) -> IngestResponse:
        p = Path(req.path)
        if not p.exists():
            raise _api_error("BAD_REQUEST", f"路径不存在: {req.path}", 400)

        # 同步执行（嵌入是 CPU 密集），用线程避免阻塞事件循环
        summary = await asyncio.to_thread(
            ingest_path,
            path=p,
            collection=req.collection,
            recursive=req.recursive,
            force=req.force,
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
        )

    # --- POST /v1/search ---
    @app.post("/v1/search", response_model=SearchResponse)
    async def search(req: SearchRequest) -> SearchResponse:
        store = state.ensure_open()
        try:
            retriever = Retriever(store=store, embedder=state.embedder)
            hits, stats = await asyncio.to_thread(
                retriever.search,
                req.query,
                req.collection,
                req.top_k,
                req.min_score,
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
        page_size: int = Query(20, ge=1, le=100),
        format: str | None = Query(None),
    ) -> ListDocumentsResponse:
        store = state.ensure_open()
        # 简化：忽略 format 过滤与排序
        offset = (page - 1) * page_size
        docs = store.list_documents(
            collection=collection, limit=page_size, offset=offset
        )
        total = store.get_stats().total_documents
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
    async def get_document(doc_id: str) -> dict:
        store = state.ensure_open()
        docs = store.list_documents(limit=10000)
        match = next((d for d in docs if d.id == doc_id), None)
        if match is None:
            raise _api_error("NOT_FOUND", f"文档不存在: {doc_id}", 404)
        return {
            "document": match.__dict__,
            "chunks_preview": [],
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
        s = store.get_stats()
        return StatsResponse(
            total_documents=s.total_documents,
            total_chunks=s.total_chunks,
            collections={
                name: [dc, cc] for name, (dc, cc) in s.collections.items()
            },
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
        return QualityResponse(
            collection=collection,
            total_documents=len(docs),
            total_chunks=total_chunks,
            format_distribution=fmt_dist,
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

        try:
            loader = get_loader(p)
            doc = loader.extract(p)
            content = convert_document(doc, req.output_format)
        except ConversionError as e:
            raise _api_error("INTERNAL", str(e), 500) from e
        except Exception as e:  # noqa: BLE001
            raise _api_error("INTERNAL", f"加载失败: {e}", 500) from e

        # 写文件（如果指定 output_path）
        if req.output_path:
            Path(req.output_path).write_text(content, encoding="utf-8")

        return ConvertResponse(
            input=p.name,
            output_format=req.output_format,
            content=content,
            elements_count=len(doc.elements),
        )

    # --- POST /v1/reindex ---
    @app.post("/v1/reindex", response_model=JobStatus)
    async def reindex(req: ReindexRequest) -> JobStatus:
        # 占位实现：创建 job 立即返回
        job_id = _new_id()
        job = JobStatus(
            job_id=job_id,
            type="reindex",
            status="completed",
            progress=1.0,
            processed=0,
            total=0,
            started_at=_now_iso(),
            finished_at=_now_iso(),
        )
        state.jobs[job_id] = job
        return job

    # --- GET /v1/jobs/{id} ---
    @app.get("/v1/jobs/{job_id}", response_model=JobStatus)
    async def get_job(job_id: str) -> JobStatus:
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
