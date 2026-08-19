"""FastAPI HTTP 服务（extras `server`）。

按 [`docs/api.md`](../../docs/api.md) 契约实现端点：
    GET    /v1/health
    POST   /v1/ingest
    POST   /v1/search
    POST   /v1/chat
    POST   /v1/chat/stream     (SSE 流式对话)
    POST   /v1/llm/test        (设置页「测试连接」)
    POST   /v1/llm/models      (设置页/对话页「获取模型列表」)
    GET    /v1/chats           (会话列表，持久化在 SQLite)
    GET    /v1/chats/{id}
    DELETE /v1/chats/{id}
    GET    /v1/documents         (列表 + 分页)
    GET    /v1/documents/{id}
    DELETE /v1/documents/{id}
    GET    /v1/stats
    GET    /v1/quality
    POST   /v1/convert
    POST   /v1/reindex
    POST   /v1/curate          (AI 整理：打标签/归类/去重/归纳，异步任务)
    GET    /v1/jobs/{id}
    GET    /v1/jobs/{id}/events   (SSE，job 进度实时流)
    GET    /v1/events             (SSE，可选)

启动：
    doc2mind serve
    uvicorn doc2mind.server.http:create_app --factory
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doc2mind.core.config import Settings, get_config_load_error, get_settings
from doc2mind.core.converter import (
    SUPPORTED_FORMATS,
    ConversionError,
    convert_document,
)
from doc2mind.core.creator import export_artifact
from doc2mind.core.embedder import get_embedder
from doc2mind.core.embedder.base import Embedder
from doc2mind.core.llm import (
    SUPPORTED_PROVIDERS,
    LLMError,
    LLMTimeoutError,
    get_llm_client,
)
from doc2mind.core.loader.detect import get_loader, is_supported
from doc2mind.core.logging_setup import setup_logging
from doc2mind.core.models import LoadedDocument
from doc2mind.core.pipeline import ingest_path, ingest_text
from doc2mind.core.rag import RagError, clear_session, rag_answer, rag_answer_stream
from doc2mind.core.retriever.search import Retriever
from doc2mind.core.store.chat_store import ChatStore, ChatStoreError
from doc2mind.core.store.graph_store import GraphStore
from doc2mind.core.store.sqlite_vec import VectorStore

logger = logging.getLogger(__name__)

# --- FastAPI / Pydantic 依赖 ---
try:
    from fastapi import (  # type: ignore[import-untyped, import-not-found]
        Body,
        FastAPI,
        HTTPException,
        Query,
    )
    from fastapi.responses import (
        StreamingResponse,  # type: ignore[import-untyped, import-not-found]
    )
except ImportError:
    FastAPI = Any  # type: ignore[misc, assignment]
    HTTPException = Exception  # type: ignore[misc, assignment]
    Query = Any  # type: ignore[misc, assignment]
    Body = Any  # type: ignore[misc, assignment]
    StreamingResponse = Any  # type: ignore[misc, assignment]

try:
    from pydantic import BaseModel, ConfigDict, Field
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "FastAPI 依赖未安装。请运行：pip install doc2mind[server]"
    ) from e


# --- 知识图谱 DTO ---
class GraphNodeDTO(BaseModel):
    id: str
    name: str
    type: str
    group: str = ""
    size: int = 1
    collection: str = "default"


class GraphEdgeDTO(BaseModel):
    from_: str = Field(..., alias="from")
    to: str
    label: str = ""

    model_config = ConfigDict(populate_by_name=True)


class GraphResponse(BaseModel):
    nodes: list[GraphNodeDTO] = []
    edges: list[GraphEdgeDTO] = []
    total_nodes: int = 0


class GraphEntityRelationDTO(BaseModel):
    relation_id: int
    from_id: str
    from_name: str
    from_type: str
    to_id: str
    to_name: str
    to_type: str
    relation: str


class GraphContextSnippetDTO(BaseModel):
    chunk_id: int
    document_id: str
    content: str
    source: str = ""
    heading: str = ""
    page: int = 0
    doc_title: str = ""
    doc_summary: str = ""


class GraphSourceDocumentDTO(BaseModel):
    source: str
    title: str
    summary: str = ""
    chunk_count: int = 0


class GraphEntityDetailDTO(BaseModel):
    entity: dict[str, Any] = {}
    relations: list[GraphEntityRelationDTO] = []
    snippets: list[GraphContextSnippetDTO] = []
    source_documents: list[GraphSourceDocumentDTO] = []


class GraphStatsDTO(BaseModel):
    entity_count: int = 0
    relation_count: int = 0
    collection: str | None = None



def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


# 搜索结果单条 content 上限：防止超大 chunk 文本拖垮前端渲染/内存
_SEARCH_CONTENT_MAX = 2000


def _truncate_search_content(content: str, max_len: int = _SEARCH_CONTENT_MAX) -> str:
    """截断搜索结果 content 到上限（避免超大文本块）。

    chunk 可能高达数千 token，直接全量下发会导致 WPF 侧渲染与内存压力；
    详情可后续通过 GET /v1/documents/{id}（chunk_content_length）取全文。
    """
    if content is None or len(content) <= max_len:
        return content
    return content[:max_len] + "…（内容过长，已截断）"


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
    # None = 落默认集合并允许 AI 自动归类（auto_curate_on_ingest 开启时）
    collection: str | None = None
    force: bool = False

    model_config = {"populate_by_name": True}


class CurateRequest(BaseModel):
    """AI 知识库整理请求（打标签/摘要/归类/语义去重/归纳合并）。"""
    # None = 整理全部集合
    collection: str | None = None
    # 默认全部四项：enrich/categorize/dedup/consolidate
    actions: list[str] | None = None
    # True = 只读预览（零写入）；删除/合并类动作需确认后再用 dry_run=False 执行
    dry_run: bool = True
    # enrich/categorize 处理的文档上限（LLM 调用成本护栏）
    top_k: int | None = Field(None, ge=1, le=200)

    model_config = {"populate_by_name": True}


class CreateCollectionRequest(BaseModel):
    """创建知识库集合（空集合占位，使其出现在集合列表并可被检索/对话勾选）。"""
    name: str

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


class ChatRequest(BaseModel):
    """RAG 对话请求。"""
    query: str
    collection: str | None = None
    # None = 用后端配置的 rag_top_k（WPF 对话页不传，避免硬编码 5 覆盖设置页）
    top_k: int | None = Field(None, ge=1, le=20, validation_alias="topK")
    chat_id: str | None = Field(None, validation_alias="chatId")
    collections: list[str] | None = Field(None, validation_alias="collections")
    # 按请求覆盖模型名（对话页快速切换模型用）；None = 用后端配置的 llm_model
    model: str | None = None
    enable_web_search: bool = Field(False, validation_alias="enableWebSearch")
    entity_context: str | None = Field(None, validation_alias="entityContext")
    persona: str | None = None
    attachments: list[str] = Field(default_factory=list, validation_alias="attachments")

    model_config = {"populate_by_name": True}


class SourceRefDTO(BaseModel):
    """引用来源。"""
    index: int
    source: str
    format: str
    chunk_id: int | None = None
    page: int | None = None
    heading: str | None = None
    score: float = 0.0
    source_type: str = Field("local", validation_alias="sourceType")  # "local" | "web"
    url: str | None = None
    title: str | None = None
    snippet: str | None = None

    model_config = {"populate_by_name": True}


class EntityDistillRequest(BaseModel):
    """实体知识卡片智能蒸馏请求。"""
    entity_id: str = Field(..., validation_alias="entityId")
    entity_name: str = Field(..., validation_alias="entityName")
    entity_type: str = Field("concept", validation_alias="entityType")
    collection: str | None = None
    dialogue_summary: str | None = Field(None, validation_alias="dialogueSummary")
    local_snippets: list[str] = Field(default_factory=list, validation_alias="localSnippets")
    web_references: list[str] = Field(default_factory=list, validation_alias="webReferences")
    model: str | None = None

    model_config = {"populate_by_name": True}


class EntityDistillResponse(BaseModel):
    """实体知识精炼卡片响应。"""
    entity_id: str = Field(..., validation_alias="entityId")
    entity_name: str = Field(..., validation_alias="entityName")
    markdown_card: str = Field(..., validation_alias="markdownCard")
    suggested_tags: list[str] = Field(default_factory=list, validation_alias="suggestedTags")
    model: str = ""

    model_config = {"populate_by_name": True}


class ChatResponse(BaseModel):
    """RAG 对话响应。"""
    answer: str
    chat_id: str
    model: str
    provider: str
    total_chunks: int = 0
    elapsed_ms: int = 0
    sources: list[SourceRefDTO] = []


class StreamChunk(BaseModel):
    """SSE 流式输出单元（token 或元数据）。"""
    token: str | None = None
    done: bool = False
    chat_id: str | None = None
    model: str | None = None
    provider: str | None = None
    total_chunks: int = 0
    elapsed_ms: int = 0
    sources: list[SourceRefDTO] = []


class ChatSessionDTO(BaseModel):
    """会话列表项。"""
    chat_id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


class ChatListResponse(BaseModel):
    """会话列表（按更新时间倒序）。"""
    chats: list[ChatSessionDTO]
    total: int


class ChatMessageDTO(BaseModel):
    """会话内单条消息。"""
    role: str
    content: str
    created_at: str
    sources: list[SourceRefDTO] = []


class ChatDetailResponse(BaseModel):
    """会话详情（全部消息，供前端回看/续聊）。"""
    chat_id: str
    title: str
    messages: list[ChatMessageDTO]


class CreativeExportRequest(BaseModel):
    """创作交付物导出请求。"""
    content: str
    format: str | None = None
    output_path: str | None = Field(None, validation_alias="outputPath")
    title: str | None = None
    theme: str | None = None

    model_config = {"populate_by_name": True}


class CreativeExportResponse(BaseModel):
    """创作交付物导出结果响应。"""
    ok: bool
    format: str
    file_path: str
    file_name: str
    file_size_bytes: int = 0
    error: str | None = None


class CreativeInspectRequest(BaseModel):
    """创作物/PPT 效果自检请求。"""
    content: str


class InspectionIssueDto(BaseModel):
    level: str
    category: str
    message: str
    slide_index: int | None = None
    fix_suggestion: str = ""


class CreativeInspectResponse(BaseModel):
    """PPT 效果自检报告响应。"""
    score: int
    grade: str
    summary: str
    slide_count: int
    notes_coverage_pct: float
    archetype_diversity: int
    total_words: int
    avg_words_per_slide: float
    issues: list[InspectionIssueDto] = []
    recommendations: list[str] = []
    highlights: list[str] = []


class ChatDeleteResponse(BaseModel):
    chat_id: str
    deleted: bool = True


# 设置页可调的后端运行参数（嵌入 + 分块 + 检索 + LLM）。
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
    # --- LLM / RAG 对话 ---
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    rag_top_k: int | None = None
    rag_min_score: float | None = None
    # 自定义 RAG 系统提示词；空字符串 = 显式清除（回到内置默认提示词）
    rag_system_prompt: str | None = None
    llm_timeout: float | None = None
    # --- 文件系统监控 ---
    watch_paths: list[str] | None = None
    watch_debounce_seconds: float | None = None

    model_config = {"populate_by_name": True}


class LlmTestRequest(BaseModel):
    """LLM 连接测试请求 — 用传入参数构造临时客户端验证，不落盘、不动运行时配置。

    字段语义：None = 沿用后端当前运行时配置的值；传值 = 测试该新值
    （WPF 设置页「测试连接」传 UI 当前输入，未保存也能测）。
    """

    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    # 测试超时秒数（连接测试宜短，避免长时间挂起）
    timeout: float = Field(15.0, gt=0, le=120)
    # True = 走流式接口（stream_chat）测试：流式特有的问题（SSE 解析、
    # chunk delta 格式）只有真流式路径才能暴露，对话页用的就是流式
    stream: bool = False

    model_config = {"populate_by_name": True}


# --- 响应体 ---
class HealthResponse(BaseModel):
    status: str = "ok"  # ok | degraded（存储不可用时）
    version: str
    uptime_seconds: int
    # 嵌入推理能力上报（WPF 据此判断是否提示 GPU 加速）
    gpu_available: bool = False
    gpu_provider: str | None = None
    embed_providers: list[str] | None = None
    # 真实健康探测：数据库连接 + sqlite-vec 扩展可用性
    store_ok: bool = True
    store_error: str | None = None


class GpuDiagnosisResponse(BaseModel):
    """GPU 加速环境诊断报告（设置页「GPU 加速」卡片）。"""

    gpu_available: bool = False
    gpu_provider: str | None = None
    embed_providers: list[str] | None = None
    has_nvidia_gpu: bool = False
    gpu_name: str | None = None
    driver_version: str | None = None
    cuda_driver_version: str | None = None
    cuda_runtime_ready: bool = False
    cuda_runtime_tag: str | None = None  # "cu12" | "cu13" | None
    python_version: str | None = None
    installed_packages: dict[str, str | None] = {}
    local_wheels_found: list[dict[str, Any]] = []
    recommended_path: str = "cpu"  # cuda12|cuda13|directml|paddle-ocr-gpu|cpu|coreml
    warnings: list[str] = []
    platform: str | None = None


class InstallGpuRequest(BaseModel):
    path: str  # cuda12|cuda13|directml|paddle-ocr-gpu

    model_config = {"populate_by_name": True}


class InstallOcrRequest(BaseModel):
    """OCR 依赖一键安装请求（与 InstallGpuRequest 同构）。"""

    path: str = "cpu"  # cpu | paddle-ocr-gpu

    model_config = {"populate_by_name": True}


class LocalAiEnvironmentResponse(BaseModel):
    """本地 AI 环境与大模型服务探测响应。"""

    ollama: dict[str, Any] = {}
    lm_studio: dict[str, Any] = {}
    local_gguf_models: list[dict[str, Any]] = []
    local_gguf_count: int = 0
    recommendations: list[dict[str, Any]] = []


class DownloadModelRequest(BaseModel):
    """嵌入模型下载请求（带进度回传）。"""

    model_name: str | None = None  # None = 用当前配置的模型

    model_config = {"populate_by_name": True}


class ConfigResponse(BaseModel):
    embed_model: str
    embed_model_path: str | None = None
    embed_batch_size: int
    chunk_max_tokens: int
    chunk_min_chars: int
    chunk_overlap_chars: int
    chunk_max_chars: int
    search_top_k: int
    rrf_k: int
    # --- LLM / RAG 对话 ---
    llm_provider: str = "none"
    llm_base_url: str | None = None
    # None = 未配置（含清除后）；前端显示为空输入
    llm_model: str | None = None
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048
    rag_top_k: int = 5
    rag_min_score: float = 0.0
    # 自定义 RAG 系统提示词；None = 未配置（用内置默认提示词）
    rag_system_prompt: str | None = None
    llm_timeout: float = 0.0
    # --- 文件系统监控 ---
    watch_paths: list[str] = []
    watch_debounce_seconds: float = 5.0
    # API key 不回传明文（前端只显示是否已配置），避免泄露到 WPF 日志/响应体
    llm_api_key_configured: bool = False
    # 可选提示（如切换模型后需要重建索引）；null 表示无提示
    notice: str | None = None
    # 启动时 config.toml 解析失败的告警（null = 配置文件正常）
    config_error: str | None = None


class LlmTestResponse(BaseModel):
    """LLM 连接测试结果。"""

    ok: bool
    provider: str = ""
    model: str = ""
    # 回复预览（成功时前 100 字符，供前端展示）
    reply_preview: str | None = None
    elapsed_ms: int = 0
    # 失败原因（已分类的中文提示：key 无效 / 地址错误 / 网络不通 / SDK 未安装 / 超时）
    error: str | None = None


class LlmModelsRequest(BaseModel):
    """列出提供商可用模型 — 与 /v1/llm/test 同语义：None = 沿用后端当前
    运行时配置；传值 = 用该新参数拉取（WPF 未保存也能先选好模型）。"""

    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    # 拉取超时秒数（列表接口宜短，避免长时间挂起）
    timeout: float = Field(10.0, gt=0, le=60)

    model_config = {"populate_by_name": True}


class LlmModelsResponse(BaseModel):
    """模型列表拉取结果。"""

    ok: bool
    provider: str = ""
    models: list[str] = []
    # 失败原因（已分类的中文提示；404 时附带「手动输入」引导）
    error: str | None = None


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
    # 入库后 AI 自动整理结果（enrich/categorize）；未触发为 None
    curation: dict[str, Any] | None = None


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
    # True = 嵌入服务不可用，本次结果为纯 BM25 降级检索
    degraded: bool = False
    # 供前端直接展示的提示（空原因 / 降级原因 / min_score 误用提醒）；null = 无
    message: str | None = None


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
    # 异步 job 完成后的详细结果列表（可选，向前兼容），由 job 线程在完成时填充。
    results: list[IngestResultDTO] = []
    # curate 类任务的完整整理报告（dry_run 预览 / 执行结果），由 job 线程填充。
    report: dict[str, Any] | None = None
    # 当前正在处理的文件名（ingest 类任务实时更新，供前端进度条显示）
    current_file: str | None = None


class SampleIngestRequest(BaseModel):
    collection: str = "default"


class SampleIngestResponse(BaseModel):
    ok: bool
    status: str
    title: str
    collection: str
    chunk_count: int
    elapsed_ms: int
    error: str | None = None


class DoctorCheckDTO(BaseModel):
    name: str
    category: str
    status: str
    message: str
    detail: str | None = None
    fix_suggestion: str | None = None


class DoctorResponse(BaseModel):
    overall_status: str
    score: int
    summary: str
    timestamp: float
    checks: list[DoctorCheckDTO]


class ApiError(BaseModel):
    code: str
    message: str
    detail: Any | None = None


# --- 全局状态（单进程内的 store / embedder）---
_SSE_CONNECTIONS: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
_sse_lock = threading.Lock()


def _broadcast_event(payload: dict[str, Any]) -> None:
    """向所有 SSE 订阅者广播事件（跨线程安全）。"""
    blob = json.dumps(payload, ensure_ascii=False)
    with _sse_lock:
        for loop, q in list(_SSE_CONNECTIONS):
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(q.put_nowait, blob)


# --- job 进度 SSE 广播（每 job 独立订阅队列）---
# job_id → list[(event_loop, queue)]；后台线程更新进度时推帧，SSE 端点消费
_JOB_QUEUES: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
_job_queues_lock = threading.Lock()


def _broadcast_job_event(job_id: str, payload: dict[str, Any]) -> None:
    """向指定 job 的 SSE 订阅者广播进度事件（跨线程安全）。"""
    blob = json.dumps(payload, ensure_ascii=False)
    with _job_queues_lock:
        subscribers = list(_JOB_QUEUES.get(job_id, []))
    for loop, q in subscribers:
        try:
            loop.call_soon_threadsafe(q.put_nowait, blob)
        except Exception:  # noqa: BLE001
            pass


def _subscribe_job(job_id: str, loop: asyncio.AbstractEventLoop, q: asyncio.Queue) -> None:
    with _job_queues_lock:
        _JOB_QUEUES.setdefault(job_id, []).append((loop, q))


def _unsubscribe_job(job_id: str, loop: asyncio.AbstractEventLoop, q: asyncio.Queue) -> None:
    with _job_queues_lock:
        subs = _JOB_QUEUES.get(job_id)
        if subs:
            with contextlib.suppress(ValueError):
                subs.remove((loop, q))
            if not subs:
                _JOB_QUEUES.pop(job_id, None)


class _AppState:
    """每个 FastAPI app 实例的共享状态。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedder: Embedder | None = None
        self.store: VectorStore | None = None
        self.file_watcher: Any | None = None
        self.jobs: dict[str, JobStatus] = {}
        self.started_at = datetime.now(timezone.utc)
        # 同步锁：ensure_open 是同步方法，并发首次请求需互斥创建 store/embedder
        self._lock = threading.Lock()
        # jobs 由 reindex 后台线程写、GET /v1/jobs 事件循环线程读，需独立锁
        self._jobs_lock = threading.Lock()
        # 全局写锁：互斥 ingest / delete / reindex，防止 reindex 重建向量表
        # （DROP vec_chunks + 回填）期间并发写落到不存在的表上。
        self._write_lock = threading.Lock()

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


def create_app(settings: Settings | None = None) -> Any:
    """创建 FastAPI app 实例。

    用 factory 模式便于测试隔离与 uvicorn 启动。
    """
    if FastAPI is Any:
        raise ImportError(
            "FastAPI 依赖未安装。请运行：pip install doc2mind[server]"
        )

    # 日志落盘（数据目录 logs/doc2mind.log，轮转）；失败退化 stderr 不阻断
    setup_logging()

    from doc2mind import __version__

    app = FastAPI(
        title="DocMind",
        description="轻量向量知识库 HTTP API",
        version=__version__,
    )
    state = _AppState()
    if settings is not None:
        state.settings = settings
    app.state.doc2mind = state

    # --- GET /v1/health ---
    @app.get("/v1/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        uptime = int((datetime.now(timezone.utc) - state.started_at).total_seconds())
        # 探测嵌入实际使用的 providers（不加载模型，轻量）
        try:
            from doc2mind.core.embedder.fastembed_impl import get_embed_providers

            providers = get_embed_providers()
        except Exception:  # noqa: BLE001 — 探测失败按 CPU 处理，不阻断健康检查
            providers = ["CPUExecutionProvider"]
        gpu = [p for p in providers if "CUDA" in p or "DML" in p]

        # 真实健康探测：数据库连接 + vec0 扩展。此前只报 uptime，
        # 数据库损坏 / sqlite-vec 缺失时依然绿灯 "ok"，用户"服务正常但全部报错"。
        store_ok, store_error = True, None
        try:
            store = await asyncio.to_thread(state.ensure_open)
            if not await asyncio.to_thread(store.ping):
                store_ok = False
                store_error = "数据库连接或 sqlite-vec 扩展不可用"
        except Exception as e:  # noqa: BLE001
            store_ok = False
            store_error = f"存储打开失败: {e}"
            logger.error("健康检查：存储探测失败：%s", e)

        return HealthResponse(
            status="ok" if store_ok else "degraded",
            version=__version__,
            uptime_seconds=uptime,
            gpu_available=bool(gpu),
            gpu_provider=gpu[0] if gpu else None,
            embed_providers=providers,
            store_ok=store_ok,
            store_error=store_error,
        )

    # --- GET /v1/doctor（系统全维体检与自愈诊断）---
    @app.get("/v1/doctor", response_model=DoctorResponse)
    async def get_doctor_report(network: bool = Query(True)) -> DoctorResponse:
        from doc2mind.core.doctor import run_diagnostics

        report = await asyncio.to_thread(run_diagnostics, check_network=network)
        return DoctorResponse(**report.to_dict())

    # --- POST /v1/sample/ingest（一键导入内置示例文档库）---
    @app.post("/v1/sample/ingest", response_model=SampleIngestResponse)
    async def ingest_sample(req: SampleIngestRequest) -> SampleIngestResponse:
        from doc2mind.core.sample_data import ingest_sample_knowledgebase

        def _do_ingest_sample() -> dict[str, Any]:
            with state._write_lock:
                return ingest_sample_knowledgebase(
                    collection=req.collection,
                    settings=state.settings,
                )

        res = await asyncio.to_thread(_do_ingest_sample)
        return SampleIngestResponse(**res)

    # --- GET /v1/system/gpu-diagnosis（设置页 GPU 加速卡片）---
    @app.get("/v1/system/gpu-diagnosis", response_model=GpuDiagnosisResponse)
    async def gpu_diagnosis() -> GpuDiagnosisResponse:
        from doc2mind.core.system_env import get_gpu_diagnosis

        return GpuDiagnosisResponse(**get_gpu_diagnosis())

    # --- POST /v1/system/install-gpu（一键安装，SSE 流式日志）---
    @app.post("/v1/system/install-gpu")
    async def install_gpu(req: InstallGpuRequest) -> Any:
        from doc2mind.core.system_env import install_gpu_packages

        async def event_generator() -> Any:
            try:
                async for event in install_gpu_packages(req.path):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:  # noqa: BLE001 — 异常以 SSE 错误帧结束
                logger.error("GPU 安装失败：%s", e)
                yield (
                    "data: "
                    + json.dumps(
                        {"type": "error", "message": f"安装异常：{e}"},
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # --- GET /v1/system/dependencies（设置页「环境自检」面板）---
    @app.get("/v1/system/dependencies")
    async def get_dependencies() -> dict[str, Any]:
        from doc2mind.core.system_env import get_dependencies_status

        return await asyncio.to_thread(get_dependencies_status)

    # --- GET /v1/system/local-ai-environment（设置页「本地 AI 智能感知」）---
    @app.get("/v1/system/local-ai-environment", response_model=LocalAiEnvironmentResponse)
    async def get_local_ai_environment_endpoint() -> LocalAiEnvironmentResponse:
        from doc2mind.core.local_ai_detect import get_local_ai_environment

        res = await get_local_ai_environment()
        return LocalAiEnvironmentResponse(**res)

    # --- POST /v1/system/install-ocr（一键安装 OCR，SSE 流式日志）---
    @app.post("/v1/system/install-ocr")
    async def install_ocr(req: InstallOcrRequest) -> Any:
        from doc2mind.core.system_env import install_ocr_packages

        async def event_generator() -> Any:
            try:
                async for event in install_ocr_packages(req.path):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:  # noqa: BLE001
                logger.error("OCR 安装失败：%s", e)
                yield (
                    "data: "
                    + json.dumps(
                        {"type": "error", "message": f"OCR 安装异常：{e}"},
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # --- POST /v1/system/download-model（嵌入模型下载，SSE 流式进度）---
    @app.post("/v1/system/download-model")
    async def download_model(req: DownloadModelRequest) -> Any:
        from doc2mind.core.config import get_settings
        from doc2mind.core.embedder.fastembed_impl import download_model_with_progress

        settings = get_settings()
        model_name = req.model_name or settings.embed_model

        async def event_generator() -> Any:
            loop = asyncio.get_running_loop()
            q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            def _on_progress(downloaded: int, total: int, dl_bytes: int, total_bytes: int) -> None:
                payload = {
                    "type": "progress",
                    "downloaded_files": downloaded,
                    "total_files": total,
                    "downloaded_bytes": dl_bytes,
                    "total_bytes": total_bytes,
                    "model": model_name,
                }
                try:
                    loop.call_soon_threadsafe(q.put_nowait, payload)
                except Exception:  # noqa: BLE001
                    pass

            # 下载在后台线程跑（同步阻塞调用），进度通过 queue 推到 SSE
            def _do_download() -> None:
                try:
                    snapshot_path = download_model_with_progress(
                        model_name=model_name,
                        settings=settings,
                        progress_cb=_on_progress,
                    )
                    loop.call_soon_threadsafe(
                        q.put_nowait,
                        {"type": "done", "model": model_name, "path": snapshot_path},
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error("模型下载失败：%s", e)
                    loop.call_soon_threadsafe(
                        q.put_nowait,
                        {"type": "error", "message": str(e), "model": model_name},
                    )

            threading.Thread(target=_do_download, daemon=True).start()

            try:
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=300.0)
                    except asyncio.TimeoutError:
                        yield (
                            "data: "
                            + json.dumps({"type": "heartbeat"}, ensure_ascii=False)
                            + "\n\n"
                        )
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("type") in ("done", "error"):
                        break
            finally:
                pass
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # --- GET /v1/jobs/{id}/events（job 进度 SSE 实时流）---
    @app.get("/v1/jobs/{job_id}/events")
    async def job_events(job_id: str) -> Any:
        """SSE 流式推送指定 job 的进度事件，替代前端轮询。"""
        with state._jobs_lock:
            job = state.jobs.get(job_id)
        if job is None:
            raise _api_error("NOT_FOUND", f"任务不存在: {job_id}", 404)

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str] = asyncio.Queue()
        _subscribe_job(job_id, loop, q)

        async def event_stream() -> Any:
            try:
                # 先发当前快照（订阅时 job 可能已有进度）
                with state._jobs_lock:
                    snap = {
                        "type": "progress",
                        "ts": _now_iso(),
                        "progress": job.progress,
                        "processed": job.processed,
                        "total": job.total,
                        "current_file": job.current_file,
                        "status": job.status,
                    }
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
                # 已是终态则立即结束
                if job.status in ("completed", "failed", "cancelled", "done", "succeeded"):
                    yield f"data: {json.dumps({'type': job.status, 'ts': _now_iso()}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                while True:
                    try:
                        blob = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {blob}\n\n"
                        # 解析判断终态
                        try:
                            evt = json.loads(blob)
                            if evt.get("type") in ("done", "failed", "cancelled"):
                                yield "data: [DONE]\n\n"
                                break
                        except json.JSONDecodeError:  # noqa: BLE001
                            pass
                    except asyncio.TimeoutError:
                        hb = json.dumps({"type": "heartbeat", "ts": _now_iso()}, ensure_ascii=False)
                        yield f"data: {hb}\n\n"
            finally:
                _unsubscribe_job(job_id, loop, q)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # --- GET/POST /v1/config（设置页：嵌入模型 + 分块 + 检索 + LLM）---
    @app.get("/v1/config", response_model=ConfigResponse)
    async def get_config() -> ConfigResponse:
        s = state.settings
        return ConfigResponse(
            embed_model=s.embed_model,
            embed_model_path=s.embed_model_path,
            embed_batch_size=s.embed_batch_size,
            chunk_max_tokens=s.chunk_max_tokens,
            chunk_min_chars=s.chunk_min_chars,
            chunk_overlap_chars=s.chunk_overlap_chars,
            chunk_max_chars=s.chunk_max_chars,
            search_top_k=s.search_top_k,
            rrf_k=s.rrf_k,
            llm_provider=s.llm_provider,
            llm_base_url=s.llm_base_url,
            llm_model=s.llm_model,
            llm_temperature=s.llm_temperature,
            llm_max_tokens=s.llm_max_tokens,
            rag_top_k=s.rag_top_k,
            rag_min_score=s.rag_min_score,
            rag_system_prompt=s.rag_system_prompt,
            llm_timeout=s.llm_timeout,
            watch_paths=list(s.watch_paths),
            watch_debounce_seconds=s.watch_debounce_seconds,
            llm_api_key_configured=bool(s.llm_api_key),
            config_error=get_config_load_error(),
        )

    @app.post("/v1/config", response_model=ConfigResponse)
    async def update_config(req: ConfigUpdate) -> ConfigResponse:
        s = state.settings
        old_model = s.embed_model

        # provider 合法性校验：配置错误尽早暴露（400），而非对话时才报错
        if req.llm_provider is not None:
            provider = req.llm_provider.strip()
            if provider not in SUPPORTED_PROVIDERS:
                raise _api_error(
                    "BAD_REQUEST",
                    f"不支持的 llm_provider: {provider}，可选: {'/'.join(SUPPORTED_PROVIDERS)}",
                    400,
                )

        # 允许修改的字段 → 更新运行时 settings（后续导入/检索生效）
        updates = req.model_dump(exclude_none=True)
        # 空字符串 = 显式清除 llm_api_key / llm_base_url / llm_model / rag_system_prompt
        # （exclude_none 会忽略 null，前端清空输入框时传 ""）
        for k in ("llm_api_key", "llm_base_url", "llm_model", "rag_system_prompt"):
            if k in updates and isinstance(updates[k], str) and not updates[k].strip():
                updates[k] = None
        if updates:
            for k, v in updates.items():
                if hasattr(s, k):
                    setattr(s, k, v)

        # 模型切换引导：维度变化时提示用户重建索引，并同步 settings.embed_dim
        # （否则本进程/其他进程后续新建 store 仍用旧预设维度，重现维度不匹配）
        notice: str | None = None
        if req.embed_model and req.embed_model != old_model:
            from doc2mind.core.embedder.catalog import get_model_info

            new_info = get_model_info(req.embed_model)
            old_info = get_model_info(old_model)
            new_dim = new_info.dim if new_info else None
            old_dim = old_info.dim if old_info else None
            if new_dim is not None:
                s.embed_dim = new_dim
            if new_dim is not None and old_dim is not None and new_dim != old_dim:
                notice = (
                    f"嵌入模型维度由 {old_dim} 变为 {new_dim}。必须先在设置页执行"
                    "「重建索引」（reindex）才能继续导入和检索——在此之前新导入会"
                    "因维度不匹配而失败，搜索会降级为纯 BM25。"
                )

        # 持久化到 config.toml（下次启动自动生效）；失败不静默——写入
        # notice 提示用户本次已生效但重启后可能回退
        try:
            from doc2mind.core.config import save_settings

            if not save_settings(s):
                msg = "配置已生效，但写入 config.toml 失败（磁盘满/权限不足），重启后可能回退"
                notice = f"{notice}；{msg}" if notice else msg
        except Exception as e:  # noqa: BLE001 — 持久化失败不影响本次运行时更新
            msg = f"配置已生效，但写入 config.toml 失败（重启后可能回退）：{e}"
            notice = f"{notice}；{msg}" if notice else msg

        return ConfigResponse(
            embed_model=s.embed_model,
            embed_model_path=s.embed_model_path,
            embed_batch_size=s.embed_batch_size,
            chunk_max_tokens=s.chunk_max_tokens,
            chunk_min_chars=s.chunk_min_chars,
            chunk_overlap_chars=s.chunk_overlap_chars,
            chunk_max_chars=s.chunk_max_chars,
            search_top_k=s.search_top_k,
            rrf_k=s.rrf_k,
            llm_provider=s.llm_provider,
            llm_base_url=s.llm_base_url,
            llm_model=s.llm_model,
            llm_temperature=s.llm_temperature,
            llm_max_tokens=s.llm_max_tokens,
            rag_top_k=s.rag_top_k,
            rag_min_score=s.rag_min_score,
            rag_system_prompt=s.rag_system_prompt,
            llm_timeout=s.llm_timeout,
            watch_paths=list(s.watch_paths),
            watch_debounce_seconds=s.watch_debounce_seconds,
            llm_api_key_configured=bool(s.llm_api_key),
            notice=notice,
        )

    # --- POST /v1/llm/test（设置页「测试连接」：验证 LLM 配置是否可用）---
    @app.post("/v1/llm/test", response_model=LlmTestResponse)
    async def llm_test(req: LlmTestRequest) -> LlmTestResponse:
        """用传入参数构造临时客户端发一条极小消息，验证 LLM 配置。

        不落盘、不修改运行时配置、不做 RAG 检索 — 纯连通性/鉴权测试。
        请求字段 None 时沿用后端当前运行时配置（可用于验证已保存配置）。
        """
        s = state.settings
        provider = (req.provider or s.llm_provider or "none").strip()
        if provider == "none":
            return LlmTestResponse(
                ok=False, provider="none",
                error="未选择 LLM 提供商（llm_provider=none），请先在设置页选择",
            )
        if provider not in SUPPORTED_PROVIDERS:
            return LlmTestResponse(
                ok=False, provider=provider,
                error=f"不支持的提供商: {provider}，可选: {'/'.join(SUPPORTED_PROVIDERS)}",
            )

        # 临时 Settings：dataclasses.replace 生成副本，不动运行时配置
        tmp = dataclasses.replace(
            s,
            llm_provider=provider,
            llm_api_key=(req.api_key or "").strip() or s.llm_api_key,
            llm_base_url=(req.base_url or "").strip() or s.llm_base_url,
            llm_model=(req.model or "").strip() or s.llm_model,
        )

        def _run() -> tuple[str, str, str]:
            """在线程池中执行：创建临时客户端并发送最小测试消息。"""
            client = get_llm_client(tmp)
            if client is None:  # pragma: no cover — provider 已校验，防御分支
                raise LLMError("LLM 客户端创建失败")
            if req.stream:
                # 流式探测：逐 token 收集到 16 个字符即停（验证 SSE 链路即可，
                # 不必等完整回复）
                parts: list[str] = []
                for tok in client.stream_chat(
                    [{"role": "user", "content": "ping"}],
                    max_tokens=16,
                    timeout=req.timeout,
                ):
                    parts.append(tok)
                    if sum(len(p) for p in parts) >= 16:
                        break
                reply = "".join(parts)
            else:
                reply = client.chat(
                    [{"role": "user", "content": "ping"}],
                    max_tokens=16,
                    timeout=req.timeout,
                )
            return client.provider, client.model_name, reply

        t0 = time.perf_counter()
        try:
            provider_used, model, reply = await asyncio.to_thread(_run)
            return LlmTestResponse(
                ok=True,
                provider=provider_used,
                model=model,
                reply_preview=(reply or "")[:100] or "（空回复）",
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
        except ImportError as e:
            # openai SDK 未安装等运行库缺失
            return LlmTestResponse(
                ok=False, provider=provider, elapsed_ms=int((time.perf_counter() - t0) * 1000),
                error=f"运行库缺失: {e}",
            )
        except LLMTimeoutError:
            return LlmTestResponse(
                ok=False, provider=provider, elapsed_ms=int((time.perf_counter() - t0) * 1000),
                error=f"连接超时（{req.timeout:.0f}s）: 网络不通、地址错误或服务无响应",
            )
        except LLMError as e:
            return LlmTestResponse(
                ok=False, provider=provider, elapsed_ms=int((time.perf_counter() - t0) * 1000),
                error=str(e),
            )

    # --- POST /v1/llm/models（设置页/对话页「获取模型列表」）---
    @app.post("/v1/llm/models", response_model=LlmModelsResponse)
    async def llm_models(req: LlmModelsRequest) -> LlmModelsResponse:
        """列出指定提供商当前可用的模型 ID（Ollama 本地模型 / 云端 /models 接口）。

        与 /v1/llm/test 同模式：用传入参数构造临时客户端拉取，不落盘、
        不修改运行时配置。请求字段 None 时沿用后端当前运行时配置。
        """
        s = state.settings
        provider = (req.provider or s.llm_provider or "none").strip()
        if provider == "none":
            return LlmModelsResponse(
                ok=False, provider="none",
                error="未选择 LLM 提供商（llm_provider=none），请先在设置页选择",
            )
        if provider not in SUPPORTED_PROVIDERS:
            return LlmModelsResponse(
                ok=False, provider=provider,
                error=f"不支持的提供商: {provider}，可选: {'/'.join(SUPPORTED_PROVIDERS)}",
            )

        # 临时 Settings：模型名用后端当前值（仅构造客户端，不影响列表结果）
        tmp = dataclasses.replace(
            s,
            llm_provider=provider,
            llm_api_key=(req.api_key or "").strip() or s.llm_api_key,
            llm_base_url=(req.base_url or "").strip() or s.llm_base_url,
        )

        def _run() -> tuple[str, list[str]]:
            client = get_llm_client(tmp)
            if client is None:  # pragma: no cover — provider 已校验，防御分支
                raise LLMError("LLM 客户端创建失败")
            return client.provider, client.list_models(timeout=req.timeout)

        try:
            provider_used, models = await asyncio.to_thread(_run)
            return LlmModelsResponse(ok=True, provider=provider_used, models=models)
        except ImportError as e:
            return LlmModelsResponse(ok=False, provider=provider, error=f"运行库缺失: {e}")
        except LLMTimeoutError as e:
            return LlmModelsResponse(ok=False, provider=provider, error=str(e))
        except LLMError as e:
            msg = str(e)
            if "404" in msg:
                # 部分 OpenAI 兼容服务（老版 DeepSeek 网关等）未实现 /models
                msg += "（该服务可能未实现列出模型接口，请手动输入模型名）"
            return LlmModelsResponse(ok=False, provider=provider, error=msg)

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

        def _do_ingest():
            # 写互斥：与 delete / reindex 串行，避免并发写冲突
            with state._write_lock:
                return ingest_path(
                    path=p,
                    collection=collection,
                    recursive=req.recursive,
                    force=req.force,
                    store=store,
                )

        summary = await asyncio.to_thread(_do_ingest)
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

        # collection=None 透传：落默认集合并允许 AI 自动归类（显式传值则尊重选择）
        store = state.ensure_open()
        result = await asyncio.to_thread(
            ingest_text,
            text=req.text,
            title=req.title or "",
            collection=req.collection,
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

    # --- POST /v1/collections（创建空知识库集合） ---
    @app.post("/v1/collections", response_model=StatsResponse)
    async def create_collection(req: CreateCollectionRequest) -> StatsResponse:
        name = (req.name or "").strip()
        if not name:
            raise _api_error("BAD_REQUEST", "集合名不能为空", 400)
        # 仅允许安全字符：字母/数字/下划线/连字符/中文，禁止路径与 SQL 注入风险字符
        if not all(c.isalnum() or c in "_- " or ("\u4e00" <= c <= "\u9fff") for c in name):
            raise _api_error(
                "BAD_REQUEST",
                "集合名仅允许中英文、数字、空格、下划线、连字符",
                400,
            )
        name = name.replace(" ", "_")

        store = state.ensure_open()
        await asyncio.to_thread(store.ensure_collection, name)
        stats = await asyncio.to_thread(store.get_stats)
        return StatsResponse(
            total_documents=stats.total_documents,
            total_chunks=stats.total_chunks,
            collections={k: list(v) for k, v in stats.collections.items()},
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

        def _check_and_update_progress(done: int, total: int) -> None:
            with state._jobs_lock:
                if job.status == "cancelled":
                    raise RuntimeError("任务已被取消")
            _update_ingest_job(state, job, done, total)

        def _run_ingest_job() -> None:
            try:
                # 写互斥：整个任务持锁，避免与 delete / reindex 并发写
                with state._write_lock:
                    summary = ingest_path(
                        path=p,
                        collection=collection,
                        recursive=req.recursive,
                        force=req.force,
                        store=store,
                        progress=_check_and_update_progress,
                    )
                with state._jobs_lock:
                    if job.status == "cancelled":
                        return
                    job.status = "completed"
                    job.progress = 1.0
                    job.processed = summary.total_documents + summary.skipped + summary.failed
                    job.finished_at = _now_iso()
                    job.current_file = None
                    # 结果明细：每个文件的最终状态（ingested / skipped / failed），
                    # 供前端轮询完成后直接展示，无需二次同步请求。
                    job.results = [
                        IngestResultDTO(**r.__dict__) for r in summary.results
                    ]
                _broadcast_job_event(job_id, {"type": "done", "ts": _now_iso(), "status": "completed", "processed": job.processed})
            except Exception as e:  # noqa: BLE001
                with state._jobs_lock:
                    if job.status == "cancelled":
                        logger.info("任务已取消并终止后台线程: %s", job_id)
                        _broadcast_job_event(job_id, {"type": "cancelled", "ts": _now_iso()})
                        return
                    if "已被取消" in str(e):
                        job.status = "cancelled"
                        _broadcast_job_event(job_id, {"type": "cancelled", "ts": _now_iso()})
                    else:
                        job.status = "failed"
                        job.error = str(e)
                        _broadcast_job_event(job_id, {"type": "failed", "ts": _now_iso(), "error": str(e)})
                    job.finished_at = _now_iso()

        threading.Thread(target=_run_ingest_job, daemon=True).start()
        return job

    # --- POST /v1/search ---
    @app.post("/v1/search", response_model=SearchResponse)
    async def search(req: SearchRequest) -> SearchResponse:
        store = state.ensure_open()
        assert state.embedder is not None
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

        # 差异化空结果提示：知识库为空 / 集合无文档 / 确实无命中，
        # 用户不用猜"是没搜到还是搜错了地方"
        message: str | None = getattr(stats, "message", None)
        if message is None:
            if not hits:
                try:
                    total_docs = await asyncio.to_thread(
                        store.count_documents, None, None
                    )
                except Exception:  # noqa: BLE001 — 提示尽力而为
                    total_docs = -1
                if total_docs == 0:
                    message = "知识库为空：请先在【导入】页添加文档"
                elif req.collection and await asyncio.to_thread(
                    store.count_documents, req.collection, None
                ) == 0:
                    message = f"集合「{req.collection}」中没有任何文档，请确认集合名是否正确"
            elif getattr(stats, "degraded_reason", None):
                message = stats.degraded_reason

        return SearchResponse(
            query=req.query,
            total=len(hits),
            elapsed_ms=stats.elapsed_ms,
            degraded=stats.degraded,
            message=message,
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
                    content=_truncate_search_content(h.chunk.content),
                )
                for h in hits
            ],
        )

    # --- POST /v1/chat ---
    @app.post("/v1/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        """RAG 对话问答：检索知识库 + 调用 LLM 生成回答。"""
        store = state.ensure_open()
        try:
            answer = await asyncio.to_thread(
                rag_answer,
                req.query,
                req.collection,
                req.top_k,
                req.chat_id,
                collections=req.collections,
                model_override=req.model,
                enable_web_search=req.enable_web_search,
                entity_context=req.entity_context,
                persona=req.persona,
                store=store,
                embedder=state.embedder,
                attachments=req.attachments,
            )
        except RagError as e:
            raise _api_error("RAG_ERROR", str(e), 400) from e
        except Exception as e:  # noqa: BLE001
            raise _api_error("INTERNAL", f"对话失败: {e}", 500) from e

        return ChatResponse(
            answer=answer.answer,
            chat_id=answer.chat_id or "",
            model=answer.model,
            provider=answer.provider,
            total_chunks=answer.total_chunks,
            elapsed_ms=answer.elapsed_ms,
            sources=[
                SourceRefDTO(
                    index=s.index,
                    source=s.source,
                    chunk_id=s.chunk_id,
                    format=s.format,
                    page=s.page,
                    heading=s.heading,
                    score=s.score,
                    source_type=getattr(s, "source_type", "local"),
                    url=getattr(s, "url", None),
                    title=getattr(s, "title", None),
                )
                for s in answer.sources
            ],
        )

    # --- POST /v1/chat/stream (SSE) ---
    @app.post("/v1/chat/stream")
    async def chat_stream(req: ChatRequest) -> Any:
        """RAG 流式对话：先检索，再 SSE 逐 token 输出 LLM 回答。

        说明：rag_answer_stream 是同步生成器（检索 + LLM），不宜在事件循环
        线程阻塞。这里用一个后台线程驱动它，逐帧把 token 推到 asyncio.Queue，
        再由 async 生成器消费并 yield，从而实现真正的逐字流式输出（而非先
        list() 收集完再发送）。当客户端断开连接时，通过 stop_event 立即中断后台线程。
        """
        async def event_generator() -> Any:
            loop = asyncio.get_event_loop()
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            store = state.ensure_open()
            stop_event = threading.Event()
            gen = rag_answer_stream(
                req.query, req.collection, req.top_k, req.chat_id,
                collections=req.collections,
                model_override=req.model,
                enable_web_search=req.enable_web_search,
                entity_context=req.entity_context,
                persona=req.persona,
                store=store,
                embedder=state.embedder,
                stop_event=stop_event,
                attachments=req.attachments,
            )

            def _pump() -> None:
                try:
                    for chunk_json in gen:
                        if stop_event.is_set():
                            break
                        asyncio.run_coroutine_threadsafe(
                            queue.put(chunk_json), loop
                        ).result()
                except RagError as e:
                    if not stop_event.is_set():
                        asyncio.run_coroutine_threadsafe(
                            queue.put(f"__ERROR__:{e}"), loop
                        ).result()
                except Exception as e:  # noqa: BLE001
                    if not stop_event.is_set():
                        asyncio.run_coroutine_threadsafe(
                            queue.put(f"__ERROR__:对话失败: {e}"), loop
                        ).result()
                finally:
                    with contextlib.suppress(Exception):
                        asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

            fut = loop.run_in_executor(None, _pump)
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        # 心跳帧：LLM 首 token 前的检索/思考期可能远超 15s，
                        # 长时间无数据会被代理/防火墙静默掐断 SSE 连接。
                        # 注释帧（冒号开头）是 SSE 标准的忽略语法，前端解析器会跳过。
                        yield ": heartbeat\n\n"
                        continue
                    if chunk is None:
                        break
                    if chunk.startswith("__ERROR__:"):
                        yield f"data: {json.dumps({'error': chunk[len('__ERROR__:'):]}, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    yield f"data: {chunk}\n\n"
            finally:
                stop_event.set()
                await fut  # 确保后台线程已退出

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # --- GET /v1/chats（会话列表，按更新时间倒序） ---
    @app.get("/v1/chats", response_model=ChatListResponse)
    async def list_chats(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> ChatListResponse:
        store = ChatStore(state.settings.db_path)
        try:
            sessions = await asyncio.to_thread(store.list_sessions, limit, offset)
        except ChatStoreError as e:
            raise _api_error("INTERNAL", f"列出会话失败: {e}", 500) from e
        return ChatListResponse(
            chats=[
                ChatSessionDTO(
                    chat_id=s.chat_id,
                    title=s.title,
                    message_count=s.message_count,
                    created_at=s.created_at,
                    updated_at=s.updated_at,
                )
                for s in sessions
            ],
            total=len(sessions),
        )

    # --- GET /v1/chats/{chat_id}（会话全部消息，回看/续聊） ---
    @app.get("/v1/chats/{chat_id}", response_model=ChatDetailResponse)
    async def get_chat(chat_id: str) -> ChatDetailResponse:
        store = ChatStore(state.settings.db_path)
        try:
            summary, messages = await asyncio.to_thread(
                lambda: (
                    store.get_session(chat_id),
                    store.get_messages(chat_id),
                )
            )
        except ChatStoreError as e:
            raise _api_error("INTERNAL", f"读取会话失败: {e}", 500) from e
        if summary is None:
            raise _api_error("NOT_FOUND", f"会话不存在: {chat_id}", 404)

        parsed_messages = []
        for m in messages:
            srcs: list[SourceRefDTO] = []
            if getattr(m, "sources_json", None):
                try:
                    raw_srcs = json.loads(m.sources_json)
                    if isinstance(raw_srcs, list):
                        srcs = [
                            SourceRefDTO(
                                index=s.get("index", idx + 1),
                                source=s.get("source", ""),
                                chunk_id=s.get("chunk_id"),
                                format=s.get("format", ""),
                                page=s.get("page"),
                                heading=s.get("heading"),
                                score=s.get("score", 0.0),
                                source_type=s.get("source_type", "local"),
                                url=s.get("url"),
                                title=s.get("title"),
                            )
                            for idx, s in enumerate(raw_srcs)
                        ]
                except Exception:  # noqa: BLE001
                    pass
            parsed_messages.append(
                ChatMessageDTO(
                    role=m.role,
                    content=m.content,
                    created_at=m.created_at,
                    sources=srcs,
                )
            )

        return ChatDetailResponse(
            chat_id=summary.chat_id,
            title=summary.title,
            messages=parsed_messages,
        )

    # --- DELETE /v1/chats/{chat_id}（删除会话：内存 + DB） ---
    @app.delete("/v1/chats/{chat_id}", response_model=ChatDeleteResponse)
    async def delete_chat(chat_id: str) -> ChatDeleteResponse:
        try:
            deleted = await asyncio.to_thread(
                clear_session, chat_id, state.settings.db_path
            )
        except Exception as e:  # noqa: BLE001 — clear_session 内部已分类，这里兜底
            raise _api_error("INTERNAL", f"删除会话失败: {e}", 500) from e
        if not deleted:
            raise _api_error("NOT_FOUND", f"会话不存在: {chat_id}", 404)
        return ChatDeleteResponse(chat_id=chat_id, deleted=True)

    # --- GET /v1/documents ---
    @app.get("/v1/documents", response_model=ListDocumentsResponse)
    async def list_documents(
        collection: str | None = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
        format: str | None = Query(None),
        q: str | None = Query(None, min_length=1, description="按文件名/标题/摘要模糊搜索"),
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
            q=q,
        )
        total = store.count_documents(collection, format, q)
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
                        "extra": c.extra_metadata,
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

        def _do_delete() -> int:
            # 写互斥：与 ingest / reindex 串行
            with state._write_lock:
                return int(store.delete_document(doc_id))

        n = await asyncio.to_thread(_do_delete)
        if n < 0:
            raise _api_error("NOT_FOUND", f"文档不存在: {doc_id}", 404)
        return DeleteResponse(id=doc_id, deleted_chunks=n)

    # --- PUT /v1/chunks/{chunk_id}/annotation（笔记批注） ---
    @app.put("/v1/chunks/{chunk_id}/annotation")
    async def upsert_chunk_annotation(chunk_id: int, body: dict = Body(...)) -> dict:
        """更新分块批注(合并到 extra JSON)。body 示例: {"text": "这是一个重要结论"}"""
        store = state.ensure_open()
        annotation = body.get("text", "")
        ok = store.update_chunk_extra(chunk_id, {"annotation": annotation})
        if not ok:
            raise _api_error("NOT_FOUND", f"分块不存在: {chunk_id}", 404)
        return {"chunk_id": chunk_id, "annotation": annotation}

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

    # --- 知识图谱端点 ---
    @app.get("/v1/graph/visualize", response_model=GraphResponse)
    async def graph_visualize(
        collection: str | None = Query(None),
        limit: int = Query(200, ge=1, le=1000),
    ) -> GraphResponse:
        """获取知识图谱可视化数据（节点与边）。"""
        def _fetch() -> GraphResponse:
            graph_store = GraphStore(state.settings.db_path)
            try:
                data = graph_store.get_graph(collection=collection, limit=limit)
                return GraphResponse.model_validate(data)
            finally:
                graph_store.close()

        return await asyncio.to_thread(_fetch)

    @app.get("/v1/graph/entities", response_model=list[GraphNodeDTO])
    async def graph_entities(
        collection: str | None = Query(None),
        limit: int = Query(200, ge=1, le=1000),
    ) -> list[GraphNodeDTO]:
        """获取知识图谱实体列表。"""
        def _fetch() -> list[GraphNodeDTO]:
            graph_store = GraphStore(state.settings.db_path)
            try:
                data = graph_store.get_graph(collection=collection, limit=limit)
                return [GraphNodeDTO.model_validate(n) for n in data.get("nodes", [])]
            finally:
                graph_store.close()

        return await asyncio.to_thread(_fetch)

    @app.get("/v1/graph/relations/{entity_id}", response_model=list[GraphEntityRelationDTO])
    async def graph_entity_relations(
        entity_id: str,
        limit: int = Query(50, ge=1, le=200),
    ) -> list[GraphEntityRelationDTO]:
        """获取指定实体的关联关系。"""
        def _fetch() -> list[GraphEntityRelationDTO]:
            graph_store = GraphStore(state.settings.db_path)
            try:
                relations = graph_store.get_entity_relations(entity_id, limit=limit)
                return [GraphEntityRelationDTO.model_validate(r) for r in relations]
            finally:
                graph_store.close()

        return await asyncio.to_thread(_fetch)

    @app.get("/v1/graph/entities/{entity_id}/details", response_model=GraphEntityDetailDTO)
    @app.get("/v1/graph/entity/{entity_id}", response_model=GraphEntityDetailDTO)
    async def graph_entity_details(
        entity_id: str,
        limit: int = Query(8, ge=1, le=50),
    ) -> GraphEntityDetailDTO:
        """获取指定实体的完整知识全景：基本信息、关联关系、来源文档和上下文内容切片。"""
        def _fetch() -> GraphEntityDetailDTO:
            graph_store = GraphStore(state.settings.db_path)
            try:
                detail = graph_store.get_entity_detail(entity_id, snippet_limit=limit)
                return GraphEntityDetailDTO.model_validate(detail)
            finally:
                graph_store.close()

        return await asyncio.to_thread(_fetch)

    @app.post("/v1/graph/entities/distill", response_model=EntityDistillResponse)
    async def graph_entity_distill(req: EntityDistillRequest) -> EntityDistillResponse:
        """知识蒸馏：将实体探讨过程、本地切片与联网资料萃取提炼为高密度结构化知识卡片。"""
        from doc2mind.core.llm.factory import get_llm_client

        s = state.settings
        if req.model:
            from dataclasses import replace as dc_replace
            s = dc_replace(s, llm_model=req.model.strip())

        llm = get_llm_client(s)
        if llm is None:
            raise _api_error("LLM_NOT_CONFIGURED", "未配置 LLM，无法生成知识精炼卡片", 400)

        # 组装蒸馏 Prompt
        snippets_text = "\n\n".join(req.local_snippets) if req.local_snippets else "（无本地切片）"
        web_text = "\n\n".join(req.web_references) if req.web_references else "（无联网资料）"
        dialogue_text = req.dialogue_summary or "（无对话历史）"

        prompt = (
            f"你是一位资深技术专家与知识工程大师。请将关于实体【{req.entity_name}】（类型: {req.entity_type}）的以下全域探讨内容，"
            f"提炼萃取为一份高密度、结构化、工业级标准的《实体知识精炼卡片》：\n\n"
            f"【本地原著切片参考】\n{snippets_text}\n\n"
            f"【多轮探讨与对话记录】\n{dialogue_text}\n\n"
            f"【联网前沿资料】\n{web_text}\n\n"
            f"【输出要求】\n"
            f"请使用标准 Markdown 格式输出，包含以下结构：\n"
            f"# 📚【知识档案】{req.entity_name}\n"
            f"## 📌 核心定义与设计定位\n"
            f"## ⚙️ 底层原理与核心工作机制\n"
            f"## 💻 工业级标准代码模板与示例（若为代码实体必须提供带注释的完整实现）\n"
            f"## ⚠️ 常见高频坑点与排错避坑指南\n"
            f"## 🌐 行业最佳实践与演进对比\n"
            f"最后附上 3-5 个推荐标签（格式：`标签：#Tag1 #Tag2 #Tag3`）。"
        )

        try:
            markdown_card = await asyncio.to_thread(
                llm.chat,
                [
                    {"role": "system", "content": "你是一位专注于构建高质量工程知识体系的专家。"},
                    {"role": "user", "content": prompt}
                ]
            )
        except Exception as e:
            raise _api_error("LLM_ERROR", f"知识卡片蒸馏失败: {e}", 500) from e

        # 提取推荐标签
        import re
        tags: list[str] = []
        tag_match = re.search(r"标签[：:]\s*(#[\w\u4e00-\u9fa5\-_]+(?:\s+#[\w\u4e00-\u9fa5\-_]+)*)", markdown_card)
        if tag_match:
            tags = [t.strip("#").strip() for t in tag_match.group(1).split() if t.strip()]
        if not tags:
            tags = [req.entity_type, req.entity_name]

        return EntityDistillResponse(
            entity_id=req.entity_id,
            entity_name=req.entity_name,
            markdown_card=markdown_card,
            suggested_tags=tags,
            model=llm.model_name,
        )

    @app.get("/v1/graph/stats", response_model=GraphStatsDTO)
    async def graph_stats(
        collection: str | None = Query(None),
    ) -> GraphStatsDTO:
        """获取知识图谱实体与关系统计。"""
        def _fetch() -> GraphStatsDTO:
            graph_store = GraphStore(state.settings.db_path)
            try:
                stats = graph_store.get_stats(collection=collection)
                return GraphStatsDTO.model_validate(stats)
            finally:
                graph_store.close()

        return await asyncio.to_thread(_fetch)

    @app.post("/v1/graph/extract")
    async def graph_extract(
        collection: str | None = Query(None),
        top_k: int = Query(20, ge=1, le=200),
    ) -> dict[str, Any]:
        """从已有文档触发实体抽取并构建知识图谱。"""
        from doc2mind.core.curator import curate
        from doc2mind.core.llm.factory import get_llm_client

        llm = get_llm_client(state.settings)
        if llm is None:
            raise _api_error(
                "BAD_REQUEST",
                "未配置 LLM，无法抽取图谱实体。请先在「设置」页配置大模型 API Key。",
                400,
            )

        store = state.ensure_open()
        embedder = state.embedder
        try:
            report = await asyncio.to_thread(
                curate,
                store=store,
                embedder=embedder,
                llm=llm,
                settings=state.settings,
                collection=collection,
                actions=["extract"],
                dry_run=False,
                top_k=top_k,
            )
            return {
                "ok": True,
                "extracted_count": len(report.extracted),
                "skipped_count": len(report.skipped),
                "errors": report.errors,
                "elapsed_ms": report.elapsed_ms,
            }
        except Exception as e:
            logger.error("图谱抽取失败: %s", e)
            raise _api_error("INTERNAL_ERROR", f"图谱抽取失败: {e}", 500) from e

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
        def _do_convert() -> tuple[LoadedDocument, str]:
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

    # --- POST /v1/creative/export ---
    @app.post("/v1/creative/export", response_model=CreativeExportResponse)
    async def creative_export(req: CreativeExportRequest) -> CreativeExportResponse:
        """将创作内容/Artifact 编译导出为指定的物理文件（PPTX/DOCX/XLSX/HTML）。"""
        if not req.content or not req.content.strip():
            raise _api_error("BAD_REQUEST", "导出内容不能为空", 400)

        def _do_export() -> CreativeExportResponse:
            res = export_artifact(
                content=req.content,
                target_format=req.format,
                output_path=req.output_path,
                title_override=req.title,
                theme=req.theme,
            )
            return CreativeExportResponse(
                ok=res.ok,
                format=res.artifact_type,
                file_path=res.file_path,
                file_name=res.file_name,
                file_size_bytes=res.file_size_bytes,
                error=res.error,
            )

        result = await asyncio.to_thread(_do_export)
        if not result.ok:
            raise _api_error("EXPORT_FAILED", f"导出失败: {result.error}", 500)
        return result

    # --- POST /v1/creative/inspect ---
    @app.post("/v1/creative/inspect", response_model=CreativeInspectResponse)
    async def inspect_creative(req: CreativeInspectRequest) -> CreativeInspectResponse:
        """对 PPT 演示文稿进行全方位效果自检与健康度评分诊断。"""
        if not req.content or not req.content.strip():
            raise _api_error("BAD_REQUEST", "待自检内容不能为空", 400)

        from doc2mind.core.creator import inspect_presentation

        def _do_inspect() -> CreativeInspectResponse:
            report = inspect_presentation(req.content)
            return CreativeInspectResponse(
                score=report.score,
                grade=report.grade,
                summary=report.summary,
                slide_count=report.slide_count,
                notes_coverage_pct=report.notes_coverage_pct,
                archetype_diversity=report.archetype_diversity,
                total_words=report.total_words,
                avg_words_per_slide=report.avg_words_per_slide,
                issues=[
                    InspectionIssueDto(
                        level=i.level.value,
                        category=i.category,
                        message=i.message,
                        slide_index=i.slide_index,
                        fix_suggestion=i.fix_suggestion,
                    )
                    for i in report.issues
                ],
                recommendations=report.recommendations,
                highlights=report.highlights,
            )

        return await asyncio.to_thread(_do_inspect)

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
        elif state.embedder is not None:
            # 未指定 model：复用当前 embedder。运行时通过 /v1/config 换过嵌入模型时，
            # 当前 embedder 维度可能与库表维度不一致（如 512 → 768），
            # 此时 update_embeddings 原地更新必然维度不匹配失败，必须同样走重建路径。
            # 先 probe 一次强制加载模型拿到真实维度（加载前 dimension 是预设值）。
            try:
                _ = list(state.embedder.embed_texts(["probe"]))
            except Exception as e:  # noqa: BLE001
                raise _api_error(
                    "BAD_REQUEST", f"加载嵌入模型失败: {e}", 400
                ) from e
            if state.embedder.dimension != store.embedding_dim:
                need_rebuild = True

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
            # 写互斥：重建向量表（DROP + 回填）期间禁止 ingest/delete 并发写
            with state._write_lock:
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
                    batch_size = 32
                    processed = 0
                    rebuild_pairs: list[tuple[int, object]] = []
                    for i in range(0, total, batch_size):
                        batch = pairs[i : i + batch_size]
                        texts = [content for _, content in batch]
                        embeddings = list(embedder.embed_texts(texts))
                        if len(embeddings) != len(batch):
                            raise RuntimeError(
                                f"嵌入数量 ({len(embeddings)}) 与批次 ({len(batch)}) 不一致"
                            )
                        new_pairs = [
                            (cid, emb) for (cid, _), emb in zip(batch, embeddings, strict=False)
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
                        # 同步 settings 并持久化：否则 GET /v1/config 仍报旧模型名，
                        # 且重启后 embedder 按旧 embed_model 加载，回到维度不匹配状态
                        state.settings.embed_model = (
                            req.model.strip() if req.model else state.settings.embed_model
                        )
                        state.settings.embed_dim = embedder.dimension
                        try:
                            from doc2mind.core.config import save_settings

                            if not save_settings(state.settings):
                                logger.warning(
                                    "reindex 后写入 config.toml 失败，重启后将回退旧嵌入模型"
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.warning("reindex 后同步配置失败：%s", e)
                except Exception as e:  # noqa: BLE001
                    with state._jobs_lock:
                        job.status = "failed"
                        job.error = str(e)
                        job.finished_at = _now_iso()

        threading.Thread(target=_run_reindex, daemon=True).start()
        return job

    # --- POST /v1/curate（AI 知识库整理：打标签/摘要/归类/去重/归纳，异步任务） ---
    @app.post("/v1/curate", response_model=JobStatus)
    async def curate_endpoint(req: CurateRequest) -> JobStatus:
        """AI 整理知识库。LLM 调用耗时，走异步 job；报告在 job.report 里取。

        dry_run 默认 True（只读预览，零写入）；dedup/consolidate 涉及删除与
        合并，确认预览无误后用 dry_run=False 执行。
        """
        store = state.ensure_open()

        # LLM 前置检查：未配置直接 400（否则任务跑一半才发现，浪费一轮轮询）
        try:
            llm = get_llm_client(state.settings)
        except LLMError as e:
            raise _api_error("BAD_REQUEST", f"LLM 配置不可用: {e}", 400) from e
        if llm is None:
            raise _api_error(
                "BAD_REQUEST",
                "未配置 LLM（llm_provider=none），整理需要大模型；"
                "请先在设置页配置 LLM 或设置 DOC2MIND_LLM_PROVIDER",
                400,
            )

        job_id = _new_id()
        job = JobStatus(
            job_id=job_id,
            type="curate",
            status="running",
            progress=0.0,
            processed=0,
            total=0,
            started_at=_now_iso(),
        )
        with state._jobs_lock:
            state.jobs[job_id] = job

        def _update_curate_job(done: int, total: int) -> None:
            with state._jobs_lock:
                job.processed = done
                job.total = total
                job.progress = round(done / total, 4) if total > 0 else 0.0

        def _run_curate() -> None:
            from doc2mind.core.curator import curate as run_curate

            try:
                # 写互斥：与 ingest / delete / reindex 串行（dry_run 虽只读，
                # 也统一持锁，避免与并发的 dedup 执行任务互相踩）
                with state._write_lock:
                    report = run_curate(
                        store=store,
                        embedder=state.embedder,
                        llm=llm,
                        settings=state.settings,
                        collection=req.collection,
                        actions=req.actions,
                        dry_run=req.dry_run,
                        top_k=req.top_k,
                        progress=_update_curate_job,
                    )
                with state._jobs_lock:
                    job.status = "completed"
                    job.progress = 1.0
                    job.finished_at = _now_iso()
                    job.report = report.to_dict()
            except Exception as e:  # noqa: BLE001
                with state._jobs_lock:
                    job.status = "failed"
                    job.error = str(e)
                    job.finished_at = _now_iso()

        threading.Thread(target=_run_curate, daemon=True).start()
        return job

    # --- GET /v1/jobs/{id} ---
    @app.get("/v1/jobs/{job_id}", response_model=JobStatus)
    async def get_job(job_id: str) -> JobStatus:
        with state._jobs_lock:
            job = state.jobs.get(job_id)
        if job is None:
            raise _api_error("NOT_FOUND", f"任务不存在: {job_id}", 404)
        return job

    # --- DELETE /v1/jobs/{id}（取消任务） ---
    @app.delete("/v1/jobs/{job_id}", response_model=JobStatus)
    async def cancel_job(job_id: str) -> JobStatus:
        """取消异步任务（设置 status 为 cancelled，后台工作线程立即中断）。"""
        with state._jobs_lock:
            job = state.jobs.get(job_id)
            if job is None:
                raise _api_error("NOT_FOUND", f"任务不存在: {job_id}", 404)
            if job.status in ("pending", "running"):
                job.status = "cancelled"
                job.finished_at = _now_iso()
        return job

    # --- GET /v1/events (SSE) ---
    @app.get("/v1/events")
    async def events() -> Any:
        """SSE 事件流广播（文件自动摄入通知、心跳等）。"""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str] = asyncio.Queue()
        entry = (loop, q)
        with _sse_lock:
            _SSE_CONNECTIONS.add(entry)

        async def event_stream() -> Any:
            try:
                # 握手就绪帧
                ready_payload = json.dumps({"type": "ready", "ts": _now_iso()}, ensure_ascii=False)
                yield f"data: {ready_payload}\n\n"
                while True:
                    try:
                        blob = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {blob}\n\n"
                    except asyncio.TimeoutError:
                        hb_payload = json.dumps({"type": "heartbeat", "ts": _now_iso()}, ensure_ascii=False)
                        yield f"data: {hb_payload}\n\n"
            finally:
                with _sse_lock:
                    _SSE_CONNECTIONS.discard(entry)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # --- 启动/关闭钩子 ---
    @app.on_event("startup")
    async def _startup() -> None:
        if state.settings.watch_paths:
            try:
                from doc2mind.core.file_watcher import FileWatcher

                state.file_watcher = FileWatcher(
                    paths=state.settings.watch_paths,
                    settings=state.settings,
                    debounce_seconds=state.settings.watch_debounce_seconds,
                    on_ingested=lambda payload: _broadcast_event({"type": "file_ingested", "ts": _now_iso(), **payload}),
                )
                state.file_watcher.start()
            except Exception as e:  # noqa: BLE001 — 监控启动失败降级，不阻断主服务
                logger.warning("启动文件系统监控异常: %s", e)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if state.file_watcher is not None:
            try:
                state.file_watcher.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("停止文件系统监控异常: %s", e)
        if state.store is not None:
            state.store.close()

    return app


# --- 辅助 ---
def _api_error(code: str, message: str, status: int) -> Any:
    """构造统一错误响应；5xx 同时落日志（排障线索）。"""
    if status >= 500:
        logger.error("API %s (%s): %s", status, code, message)
    return HTTPException(
        status_code=status,
        detail=ApiError(code=code, message=message).model_dump(),
    )


def _update_ingest_job(
    state: _AppState,
    job: JobStatus,
    done: int,
    total: int,
    current_file: str | None = None,
) -> None:
    """异步 ingest 的进度回调：更新 job.processed / progress 并广播 SSE 事件（线程安全）。"""
    with state._jobs_lock:
        job.processed = done
        job.total = total
        job.progress = round(done / total, 4) if total > 0 else 0.0
        if current_file is not None:
            job.current_file = current_file
        snapshot = {
            "progress": job.progress,
            "processed": job.processed,
            "total": job.total,
            "current_file": job.current_file,
            "status": job.status,
        }
    _broadcast_job_event(job.job_id, {"type": "progress", "ts": _now_iso(), **snapshot})
