"""Documentation."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import platform
import secrets
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doc2mind import __version__
from doc2mind.core.backup import BackupError, create_backup, restore_backup
from doc2mind.core.config import (
    Settings,
    _user_data_dir,
    get_config_load_error,
    get_settings,
    parse_rrf_weights,
)
from doc2mind.core.converter import (
    SUPPORTED_FORMATS,
    ConversionError,
    convert_document,
)
from doc2mind.core.creator import export_artifact
from doc2mind.core.creator.history import (
    ArtifactHistoryError,
    get_version,
    list_versions,
    save_version,
)
from doc2mind.core.embedder import get_embedder
from doc2mind.core.embedder.base import Embedder
from doc2mind.core.job_store import InMemoryJobStore, JobStore
from doc2mind.core.llm import (
    SUPPORTED_PROVIDERS,
    LLMError,
    LLMTimeoutError,
    get_llm_client,
)
from doc2mind.core.loader.detect import get_loader, is_supported
from doc2mind.core.logging_setup import setup_logging
from doc2mind.core.models import LoadedDocument
from doc2mind.core.pipeline import (
    IngestCancelled as _JobCancelledError,
)
from doc2mind.core.pipeline import (
    ingest_path,
    ingest_text,
    run_background_curate,
    stage_fraction,
)
from doc2mind.core.rag import RagError, clear_session, rag_answer, rag_answer_stream
from doc2mind.core.reranker import get_reranker
from doc2mind.core.retriever.search import Retriever
from doc2mind.core.store import KnowledgeStore, StoreError, create_knowledge_store
from doc2mind.core.store.chat_store import ChatStore, ChatStoreError
from doc2mind.core.store.graph_store import GraphStore

logger = logging.getLogger(__name__)

# --- FastAPI / Pydantic 依赖 ---
try:
    from fastapi import (  # type: ignore[import-untyped, import-not-found]
        Body,
        FastAPI,
        HTTPException,
        Query,
        Request,
    )
    from fastapi.responses import (
        JSONResponse,  # type: ignore[import-untyped, import-not-found]
        StreamingResponse,  # type: ignore[import-untyped, import-not-found]
    )
except ImportError:
    FastAPI = Any  # type: ignore[misc, assignment]
    HTTPException = Exception  # type: ignore[misc, assignment]
    Query = Any  # type: ignore[misc, assignment]
    Body = Any  # type: ignore[misc, assignment]
    Request = Any  # type: ignore[misc, assignment]
    JSONResponse = Any  # type: ignore[misc, assignment]
    StreamingResponse = Any  # type: ignore[misc, assignment]

try:
    from pydantic import AliasChoices, BaseModel, ConfigDict, Field
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "FastAPI Xip install doc2mind[server]"
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
    chunk_index: int = 0
    doc_type: str = ""
    evidence_reason: str = ""
    sheet: str = ""
    slide: int = 0


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
    """Documentation."""
    if content is None or len(content) <= max_len:
        return content
    return content[:max_len] + "... (content truncated)"


# --- 请求?---
class IngestRequest(BaseModel):
    path: str
    # ?nullW Optional?None ?"default"
    collection: str | None = "default"
    recursive: bool = False
    force: bool = False

    model_config = {"populate_by_name": True}


class IngestTextRequest(BaseModel):
    """Documentation."""
    text: str
    title: str | None = None
    # None = ?AI auto_curate_on_ingest ?
    collection: str | None = None
    force: bool = False

    model_config = {"populate_by_name": True}


class CurateRequest(BaseModel):
    """Documentation."""
    # None = 整理全部集合
    collection: str | None = None
    # 全部四项：enrich/categorize/dedup/consolidate
    actions: list[str] | None = None
    # True = /?dry_run=False 
    dry_run: bool = True
    # enrich/categorize 处理的文档上限（LLM 调用成本护栏）
    top_k: int | None = Field(None, ge=1, le=200)
    # 触发来源（如 "agent_settle" / "user_manual" / "ingest_auto"），写入 curate_runs.note 便于追溯
    note: str | None = None

    model_config = {"populate_by_name": True}


class CreateCollectionRequest(BaseModel):
    """Documentation."""
    name: str

    model_config = {"populate_by_name": True}


class SearchRequest(BaseModel):
    query: str
    # 前端可能传 null（用户未填集合，表示跨全部集合），容错为 Optional
    collection: str | None = None
    top_k: int = Field(10, ge=1, le=100, validation_alias="topK")
    # ?nullJ?Optional
    min_score: float | None = Field(None, ge=0.0, le=1.0, validation_alias="minScore")
    filter: dict[str, Any] | None = None
    highlight: bool = False
    rerank: bool | None = None

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


class ProviderConfigIn(BaseModel):
    """Documentation."""

    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    model_config = {"populate_by_name": True}


def _build_llm_client_from_provider_config(
    pc: ProviderConfigIn | None, s: Settings, model_override: str | None = None
) -> Any:
    """Documentation."""
    if pc is not None:
        provider = (pc.provider or s.llm_provider or "none").strip()
        if provider != "none":
            tmp = dataclasses.replace(
                s,
                llm_provider=provider,
                llm_api_key=(pc.api_key or "").strip() or s.llm_api_key,
                llm_base_url=(pc.base_url or "").strip() or s.llm_base_url,
                llm_model=(pc.model or model_override or "").strip() or s.llm_model,
                llm_temperature=(
                    pc.temperature if pc.temperature is not None else s.llm_temperature
                ),
                llm_max_tokens=(
                    pc.max_tokens if pc.max_tokens is not None else s.llm_max_tokens
                ),
            )
            return get_llm_client(tmp)

    if model_override:
        tmp = dataclasses.replace(s, llm_model=model_override.strip())
        return get_llm_client(tmp)

    return get_llm_client(s)


def _build_llm_client_from_provider(
    req: ChatRequest, s: Settings
) -> Any:
    """Documentation."""
    if req.provider_config is None:
        return None
    return _build_llm_client_from_provider_config(req.provider_config, s, req.model)


class ChatRequest(BaseModel):
    """Documentation."""
    query: str
    collection: str | None = None
    # None = u[?rag_top_kWPF Ye~?5 ?
    top_k: int | None = Field(None, ge=1, le=20, validation_alias="topK")
    chat_id: str | None = Field(None, validation_alias="chatId")
    collections: list[str] | None = Field(None, validation_alias="collections")
    # 按请求覆盖模型名（对话页快速切换模型用）；None = 用后端配置的 llm_model
    model: str | None = None
    # Xprovider/key/url/??tokenNone = u[?
    # ?
    provider_config: ProviderConfigIn | None = Field(None, validation_alias="providerConfig")
    enable_web_search: bool = Field(False, validation_alias="enableWebSearch")
    # normal| deep enableWebSearch=true ?
    web_search_mode: str | None = Field(
        None, validation_alias=AliasChoices("webSearchMode", "web_search_mode")
    )

    def resolved_web_search_mode(self) -> str:
        """Documentation."""
        if not self.enable_web_search:
            return "off"
        raw = (self.web_search_mode or "").strip().lower()
        return "deep" if raw == "deep" else "normal"
    entity_context: str | None = Field(None, validation_alias="entityContext")
    persona: str | None = None
    # Upersona  _PERSONA_PROMPTS 
    # 兼?camelCase（WPF JsonPropertyName?snake_case 两户?
    persona_prompt: str | None = Field(
        None, validation_alias=AliasChoices("personaPrompt", "persona_prompt")
    )
    # k?null?list[str] 
    # 收到 null 触发 pydantic 422 "Input should be a valid list"，导致对话直接报错）
    attachments: list[str] | None = Field(None, validation_alias="attachments")
    # 按请求携带的 GitHub 个人令牌（仅联网搜索的 GitHub 通道用）：
    # 每个用户填自己的 Token 随请求发送，后端绝不把某用户的 Token 作为
    # qu?0 ??
    github_token: str | None = Field(None, validation_alias="githubToken")
    # RAG ?hybrid"w?"strict"?
    rag_mode: str | None = Field(None, validation_alias="ragMode")
    # uJ~?query
    memory_context: str | None = Field(None, validation_alias="memoryContext")
    # 研究任务规格，独立于 query，避免把目标/格式等元数据污染检索。
    task_context: dict[str, Any] | None = Field(
        None, validation_alias=AliasChoices("taskContext", "task_context")
    )
    # P0rag | delivery | None(?
    response_mode: str | None = Field(
        None, validation_alias=AliasChoices("responseMode", "response_mode")
    )
    # markdown | htmlhtml = ?HTML ?
    answer_format: str | None = Field(
        None, validation_alias=AliasChoices("answerFormat", "answer_format")
    )
    # ?
    continue_writing: bool = Field(
        False, validation_alias=AliasChoices("continueWriting", "continue_writing")
    )
    # Agent P1?+ ?+ ?
    agent_mode: bool = Field(
        False, validation_alias=AliasChoices("agentMode", "agent_mode", "mode_agent")
    )
    # 兼：mode ?"agent" 也进?Agent 
    mode: str | None = Field(None, validation_alias=AliasChoices("mode", "response_track"))
    # 回答模式ag | agent | auto；{级高?agentMode/全
    chat_mode: str | None = Field(
        None, validation_alias=AliasChoices("chatMode", "chat_mode", "answerMode")
    )

    model_config = {"populate_by_name": True}

    def is_agent_mode(self) -> bool:
        if self.chat_mode and self.chat_mode.strip().lower() in ("agent", "rag", "auto"):
            return self.chat_mode.strip().lower() == "agent"
        if self.agent_mode:
            return True
        return (self.mode or "").strip().lower() == "agent"

    def requested_chat_mode(self) -> str | None:
        raw = (self.chat_mode or "").strip().lower()
        if raw in ("rag", "agent", "auto"):
            return raw
        if self.agent_mode or (self.mode or "").strip().lower() == "agent":
            return "agent"
        return None


class SourceRefDTO(BaseModel):
    """Documentation."""
    index: int
    source: str
    format: str
    chunk_id: int | None = None
    page: int | None = None
    heading: str | None = None
    score: float = 0.0
    # score 量纲：rerank/vector/bm25/rrf/web_relevance/attachment；?= 旧数?
    score_type: str = ""
    # ?????{?
    confidence_label: str = ""
    source_type: str = Field("local", validation_alias="sourceType")  # "local" | "web"
    url: str | None = None
    title: str | None = None
    snippet: str | None = None
    source_name: str | None = None  # 搜索引擎来源 (DuckDuckGo / WebSearch)
    domain: str | None = None
    published_at: str | None = None
    content_fetched: bool = False
    corroborated_by: int = 0
    evidence_level: str = "unknown"

    model_config = {"populate_by_name": True}


class EntityDistillRequest(BaseModel):
    """Documentation."""
    entity_id: str = Field(..., validation_alias="entityId")
    entity_name: str = Field(..., validation_alias="entityName")
    entity_type: str = Field("concept", validation_alias="entityType")
    collection: str | None = None
    dialogue_summary: str | None = Field(None, validation_alias="dialogueSummary")
    local_snippets: list[str] = Field(default_factory=list, validation_alias="localSnippets")
    web_references: list[str] = Field(default_factory=list, validation_alias="webReferences")
    model: str | None = None
    provider_config: ProviderConfigIn | None = Field(None, validation_alias="providerConfig")

    model_config = {"populate_by_name": True}


class EntityDistillResponse(BaseModel):
    """Documentation."""
    entity_id: str = Field(..., validation_alias="entityId")
    entity_name: str = Field(..., validation_alias="entityName")
    markdown_card: str = Field(..., validation_alias="markdownCard")
    suggested_tags: list[str] = Field(default_factory=list, validation_alias="suggestedTags")
    model: str = ""

    model_config = {"populate_by_name": True}


class EvidenceSummaryDTO(BaseModel):
    """Documentation."""
    local_count: int = Field(0, validation_alias="localCount")
    web_fetched_count: int = Field(0, validation_alias="webFetchedCount")
    web_unfetched_count: int = Field(0, validation_alias="webUnfetchedCount")
    graph_injected: bool = Field(False, validation_alias="graphInjected")
    fallback_general_knowledge: bool = Field(False, validation_alias="fallbackGeneralKnowledge")
    # ?[n] cited/valid/invalid/okd?
    citation_audit: dict[str, Any] | None = None
    # IT3cite/bg/discarded ?
    citation_gate: dict[str, Any] | None = None
    # 分阶段（?联?首token/生?k?
    timing: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class ChatResponse(BaseModel):
    """Documentation."""
    answer: str
    chat_id: str
    model: str
    provider: str
    total_chunks: int = 0
    elapsed_ms: int = 0
    sources: list[SourceRefDTO] = []
    evidence: EvidenceSummaryDTO | None = None
    # 分阶段（?SSE done ?timing 对齐?
    timing: dict[str, Any] = Field(default_factory=dict)


class AgentJobResultDTO(BaseModel):
    """Terminal data retained for a completed Agent background job."""

    answer: str = ""
    chat_id: str = ""
    model: str = ""
    provider: str = ""
    total_chunks: int = 0
    elapsed_ms: int = 0
    sources: list[SourceRefDTO] = []
    trajectory: list[dict[str, Any]] = []
    trajectory_warning: str | None = None
    partial: bool = False
    warning: str | None = None
    artifacts: list[dict[str, Any]] = []


class ChatSessionDTO(BaseModel):
    """Documentation."""
    chat_id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


class ChatListResponse(BaseModel):
    """Documentation."""
    chats: list[ChatSessionDTO]
    total: int


class ChatMessageDTO(BaseModel):
    """Documentation."""
    role: str
    content: str
    created_at: str
    sources: list[SourceRefDTO] = []
    trajectory: list[dict[str, Any]] = []
    trajectory_warning: str | None = None


class ChatDetailResponse(BaseModel):
    """Documentation."""
    chat_id: str
    title: str
    messages: list[ChatMessageDTO]


class CreativeExportRequest(BaseModel):
    """Documentation."""
    content: str
    format: str | None = None
    output_path: str | None = Field(None, validation_alias="outputPath")
    title: str | None = None
    theme: str | None = None
    # UJtheme primary/bg/secondary/...
    theme_colors: dict | None = Field(
        None, validation_alias=AliasChoices("themeColors", "theme_colors")
    )

    model_config = {"populate_by_name": True}


class CreativeExportResponse(BaseModel):
    """Documentation."""
    ok: bool
    format: str
    file_path: str
    file_name: str
    file_size_bytes: int = 0
    error: str | None = None


class ArtifactVersionRequest(BaseModel):
    """A durable, restorable creative artifact draft."""

    content: str
    format: str | None = None
    title: str | None = None
    sources: list[dict[str, Any]] = []


class ArtifactVersionSummary(BaseModel):
    version_id: str
    artifact_id: str
    version: int
    created_at: str
    format: str | None = None
    title: str | None = None
    sources: list[dict[str, Any]] = []


class ArtifactVersionResponse(ArtifactVersionSummary):
    content: str


class CreativeInspectRequest(BaseModel):
    """Documentation."""
    content: str


class InspectionIssueDto(BaseModel):
    level: str
    category: str
    message: str
    slide_index: int | None = None
    fix_suggestion: str = ""


class CreativeInspectResponse(BaseModel):
    """Documentation."""
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
    # N`DOC2MIND_EMBED_MODEL_PATH?= N?
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
    rag_mode: str | None = None
    # U?RAG a?= ?
    rag_system_prompt: str | None = None
    # 多轮历史 token 预算? = 不?token 或?
    rag_max_history_tokens: int | None = None
    # --- 索后重排（Reranker / cross-encoder?--
    rerank_enabled: bool | None = None
    rerank_model: str | None = None
    rerank_recall: int | None = None
    llm_timeout: float | None = Field(None, ge=0, le=3600)
    # ?+ d?
    web_search_timeout: float | None = Field(None, ge=4, le=120)
    # T6 搜索插：builtin | tavily | bocha | serpapi（?key 回?builtin?
    search_provider: str | None = None
    search_provider_api_key: str | None = None
    search_provider_endpoint: str | None = None
    # T3/T5 门控与阶段
    citation_min_score: float | None = Field(None, ge=0.0, le=1.0)
    citation_bg_score_ratio: float | None = Field(None, ge=0.0, le=1.0)
    background_hit_limit: int | None = Field(None, ge=0, le=64)
    stage_elapsed_enabled: bool | None = None
    llm_first_token_slow_ms: int | None = Field(None, ge=1000, le=120000)
    # T8 Agent 原?tool-calling（仍?agent_mode_enabled 关束?
    agent_mode_enabled: bool | None = None
    agent_native_tool_calling: bool | None = None
    agent_file_write_policy: str | None = None
    # ag | agent | autoD?rag?
    chat_mode_default: str | None = None
    chat_mode_auto_enabled: bool | None = None
    # T9/T10/T12??RAG v?
    mcp_client_enabled: bool | None = None
    mcp_servers_json: str | None = None
    longform_enabled: bool | None = None
    longform_max_sections: int | None = Field(None, ge=2, le=16)
    longform_max_chars: int | None = Field(None, ge=1000, le=100000)
    thinking_meta_filter_enabled: bool | None = None
    # --- 文件系统监?---
    watch_paths: list[str] | None = None
    watch_debounce_seconds: float | None = None
    #  AI enrich + ?categorize + extract?
    # dedup / consolidate 永不自动跑，必须 dry_run=true 预览 + 用户确认
    auto_curate_on_ingest: bool | None = None
    # 持久化开兼True（）= 运时生?+ ?config.toml?
    # False = 只更新运行时 settings，不落盘（前端「输入即生效」的即时推送用，保存才持久化）
    persist: bool = True

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


# --- 响应?---
class HealthResponse(BaseModel):
    status: str = "ok"  # ok | degraded（存储不可用时）
    version: str
    uptime_seconds: int
    # WPF Sa GPU ?
    gpu_available: bool = False
    gpu_provider: str | None = None
    embed_providers: list[str] | None = None
    # 真实健康探测：数据库连接 + sqlite-vec 扩展可用性
    store_ok: bool = True
    store_error: str | None = None


class GpuDiagnosisResponse(BaseModel):
    """Documentation."""

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
    path: str  # cuda12|cuda13|directml|paddle-ocr-gpuOCR S /v1/system/install-ocr?

    model_config = {"populate_by_name": True}


class InstallOcrRequest(BaseModel):
    """Documentation."""

    path: str = "cpu"  # paddle-ocr-cpu | ocr-cpu | ocr | cpu
    force: bool = False  # True 时跳过已安装检查强制重装

    model_config = {"populate_by_name": True}


# 安装路径归属校验（防止 install-gpu 传 "cpu" 静默装上 Paddle 的语义串味）
_GPU_INSTALL_PATHS = {"cuda12", "cu12", "cuda13", "cu13", "directml", "paddle-ocr-gpu"}
_OCR_INSTALL_PATHS = {"paddle-ocr-cpu", "ocr-cpu", "ocr", "cpu"}

# MXpip ?
_install_lock = asyncio.Lock()


def _backend_locks_heavy_runtime() -> bool:
    """Documentation."""
    import sys as _sys

    return any(m in _sys.modules for m in ("numpy", "paddle", "onnxruntime"))


def _install_busy_error(label: str) -> dict[str, str]:
    return {
        "type": "error",
        "message": f"Another installation is already running: {label}",
    }


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

_HEAVY_RUNTIME_LOCKED_MESSAGE = (
    "当前后端进程已加载 numpy / paddle / onnxruntime 运行库，"
    "在运行中的后端内执行 pip 安装会因 Windows 文件锁失败（WinError 5）。"
    "请通过客户端设置页安装（客户端会先停止后端、以独立进程安装、再自动重启），"
    "或手动停止后端后运行：python -m doc2mind.install_cli <方案路径>"
)


async def _install_error_stream(message: str) -> Any:
    """Documentation."""
    yield (
        "data: "
        + json.dumps({"type": "error", "message": message}, ensure_ascii=False)
        + "\n\n"
    )
    yield "data: [DONE]\n\n"


class LocalAiEnvironmentResponse(BaseModel):
    """Documentation."""

    ollama: dict[str, Any] = {}
    lm_studio: dict[str, Any] = {}
    local_gguf_models: list[dict[str, Any]] = []
    local_gguf_count: int = 0
    recommendations: list[dict[str, Any]] = []


class DownloadModelRequest(BaseModel):
    """Documentation."""

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
    llm_max_tokens: int = 8192
    rag_top_k: int = 5
    rag_min_score: float = 0.0
    rag_mode: str = "strict"
    # U?RAG aNone = AXu?
    rag_system_prompt: str | None = None
    # 多轮历史 token 预算? = 不?token 或?
    rag_max_history_tokens: int = 4096
    # --- 索后重排（Reranker / cross-encoder?--
    rerank_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-base"
    rerank_recall: int = 20
    llm_timeout: float = 0.0
    web_search_timeout: float = 36.0
    # T6 搜索插（密钥只回?configured，不回显明文?
    search_provider: str = "builtin"
    search_provider_api_key_configured: bool = False
    search_provider_endpoint: str | None = None
    # T3/T5 门控与阶段?
    citation_min_score: float = 0.45
    citation_bg_score_ratio: float = 0.6
    background_hit_limit: int = 5
    stage_elapsed_enabled: bool = True
    llm_first_token_slow_ms: int = 30000
    # --- 文件系统监?---
    watch_paths: list[str] = []
    watch_debounce_seconds: float = 5.0
    # API key Y?WPF ??
    llm_api_key_configured: bool = False
    # null ?
    notice: str | None = None
    # 启动时 config.toml 解析失败的告警（null = 配置文件正常）
    config_error: str | None = None
    # Agent 升级预（阶关闭?
    agent_mode_enabled: bool = False
    agent_file_write_policy: str = "session_allow"
    agent_native_tool_calling: bool = True
    # D?rag?
    chat_mode_default: str = "rag"
    chat_mode_auto_enabled: bool = True
    auto_curate_on_ingest: bool | None = None
    # T9/T10/T12
    mcp_client_enabled: bool = False
    mcp_servers_configured: bool = False
    longform_enabled: bool = False
    longform_max_sections: int = 8
    longform_max_chars: int = 12000
    thinking_meta_filter_enabled: bool = True


class LlmTestResponse(BaseModel):
    """Documentation."""

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


class LlmModelMetaDTO(BaseModel):
    """单模型元数据（上下文窗口、最大输出上限、是否深度推理模型等）。由后端智能注册表感知。"""

    context_window: int | None = None
    max_output_tokens: int | None = None
    is_reasoning_model: bool = False
    display_name: str | None = None
    summary_text: str | None = None


class LlmModelsResponse(BaseModel):
    """模型列表拉取结果。"""

    ok: bool
    provider: str = ""
    models: list[str] = []
    # 模型名 → 元数据（context_window、max_output_tokens、summary_text 等）
    model_meta: dict[str, LlmModelMetaDTO] = {}
    # 失败原因（已分类的中文提示；404 时附带「手动输入」引导）
    error: str | None = None


def _model_context_window(model: str) -> int | None:
    """Documentation."""
    from doc2mind.core.llm.model_registry import get_model_spec
    return get_model_spec(model).context_window


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
    source_missing: bool = False


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
    # ?AI enrich/categorizeV None
    curation: dict[str, Any] | None = None
    # ?+ ?+ v?
    long_chunk_count: int = 0
    health_warnings: list[str] = []
    suggest_query: str | None = None


class IngestResponse(BaseModel):
    ingested: list[IngestResultDTO]
    skipped: int
    failed: int
    # 失败明细：每两败文件的 source + error（status="failed" ?IngestResultDTO?
    # ?a?^?
    failed_details: list[IngestResultDTO] = []
    total_documents: int
    total_chunks: int
    # j dashboard?
    long_chunk_count: int = 0
    health_warnings: list[str] = []
    suggest_query: str | None = None


class SearchHitDTO(BaseModel):
    rank: int
    score: float
    match_type: str
    vector_score: float
    bm25_score: float
    # ?sigmoid ?0-1None=?
    rerank_score: float | None = None
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
    # n?/  / min_score null = ?
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


class BackupCreateRequest(BaseModel):
    output_path: str | None = Field(None, validation_alias=AliasChoices("output_path", "outputPath"))
    include_logs: bool = Field(False, validation_alias=AliasChoices("include_logs", "includeLogs"))

    model_config = ConfigDict(populate_by_name=True)


class BackupRestoreRequest(BaseModel):
    backup_path: str = Field(validation_alias=AliasChoices("backup_path", "backupPath"))

    model_config = ConfigDict(populate_by_name=True)


class DiagnosticBundleRequest(BaseModel):
    output_path: str | None = Field(None, validation_alias=AliasChoices("output_path", "outputPath"))
    include_logs: bool = Field(False, validation_alias=AliasChoices("include_logs", "includeLogs"))

    model_config = ConfigDict(populate_by_name=True)


class BackupResponse(BaseModel):
    ok: bool
    path: str = ""
    created_at: str | None = None
    database_size: int = 0
    tables: list[str] = []
    previous_backup: str | None = None
    error: str | None = None


class ConvertResponse(BaseModel):
    input: str
    output_format: str
    content: str
    elements_count: int


class DeleteResponse(BaseModel):
    id: str
    deleted_chunks: int
    status: str = "deleted"


class RestoreResponse(BaseModel):
    """Documentation."""

    id: str
    status: str = "restored"  # restored | not_found
    note: str  # 给前端的提示（恢复后 chunks/向量已被物理删，需要重新索引）


class TrashItem(BaseModel):
    document_id: str
    source: str
    collection: str
    deleted_at: str
    purged_at: str | None = None


class TrashListResponse(BaseModel):
    items: list[TrashItem]
    total: int


class PurgeTrashRequest(BaseModel):
    older_than_days: int = Field(30, ge=1, le=365)


class PurgeTrashResponse(BaseModel):
    purged: int


class CurateRunItem(BaseModel):
    id: int
    started_at: str
    finished_at: str
    dry_run: bool
    collection: str | None = None
    actions: list[str] = []
    changed_doc_ids: list[str] = []
    skipped_count: int = 0
    error_count: int = 0
    elapsed_ms: int = 0
    note: str | None = None


class CurateRunsResponse(BaseModel):
    items: list[CurateRunItem]
    total: int


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
    # arsing/chunking/embedding/writing/curating?
    # 单大文件导入时 processed/total 长时间不动，前端靠 stage 展示当前阶段。
    stage: str | None = None
    # 阶内进?0.0~1.0（embedding 按次有w值，其余阶?None?
    stage_progress: float | None = None
    # 阶段内人话细节（如「第 3/12 页」），供前端展示解析页码等真实进度。
    stage_detail: str | None = None
    # 异步 job 完成后的详细结果列表（可选，向前兼容），由 job 线程在完成时填充。
    results: list[IngestResultDTO] = []
    # curate dry_run ?/ ?job F?
    report: dict[str, Any] | None = None
    # ingest ?
    current_file: str | None = None
    # FC-01b：取消的人话明（如已取消， N 篇已入」）
    cancel_note: str | None = None
    # Agent 后台任务完成后的可重连结果；其他 job 类型保持为空。
    permission_request_id: str | None = None
    permission_tool_id: str | None = None
    permission_timeout_sec: int | None = None
    result: AgentJobResultDTO | None = None


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


# --- 全k（单进程?store / embedder?--
_SSE_CONNECTIONS: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
_sse_lock = threading.Lock()


def _broadcast_event(payload: dict[str, Any]) -> None:
    """Documentation."""
    blob = json.dumps(payload, ensure_ascii=False)
    with _sse_lock:
        for loop, q in list(_SSE_CONNECTIONS):
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(q.put_nowait, blob)


# --- job ?SSE  job J?--
# job_id ?list[(event_loop, queue)]FSE X
_JOB_QUEUES: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
_job_queues_lock = threading.Lock()

# job ?TTL job?results/curation
# ?
_JOB_MAX_KEEP = int(os.getenv("DOC2MIND_JOB_MAX_KEEP", "50"))
_JOB_TTL_SECONDS = int(os.getenv("DOC2MIND_JOB_TTL_SECONDS", str(30 * 60)))


def _broadcast_job_event(job_id: str, payload: dict[str, Any]) -> None:
    """Documentation."""
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


#  = pipeline.IngestCancelled??
# pipeline 内部抛出的取消也由 except _JobCancelledError 统一捕获）


class _AppState:
    """Documentation."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedder: Embedder | None = None
        self.store: KnowledgeStore | None = None
        self.file_watcher: Any | None = None
        self.jobs: JobStore[JobStatus] = InMemoryJobStore()
        self.job_cancel_events: dict[str, threading.Event] = {}
        self.started_at = datetime.now(timezone.utc)
        # 同步锁：ensure_open 是同步方法，并发首次请求需互斥创建 store/embedder
        self._lock = threading.Lock()
        # jobs ?reindex ET /v1/jobs J?
        self._jobs_lock = threading.Lock()
        # 全锁：互?ingest / delete / reindex，防?reindex 重建向量?
        # （DROP vec_chunks + 回填）期间并发写到不存在的表上?
        self._write_lock = threading.Lock()
        self._db_request_lock = threading.Lock()
        self._active_db_requests = 0
        self._restore_in_progress = False

    def ensure_open(self) -> KnowledgeStore:
        # 双检锁：避免并发首次请求重复创建 store/embedder
        if self.store is not None and self.embedder is not None:
            return self.store

        with self._lock:
            if self.embedder is None:
                self.embedder = get_embedder(self.settings)
            if self.store is None:
                self.store = create_knowledge_store(
                    self.settings, self.embedder.dimension
                )
                self.store.open()
            return self.store

    def begin_db_request(self) -> bool:
        """Register a normal database request unless restore is in progress."""
        with self._db_request_lock:
            if self._restore_in_progress:
                return False
            self._active_db_requests += 1
            return True

    def end_db_request(self) -> None:
        with self._db_request_lock:
            self._active_db_requests = max(0, self._active_db_requests - 1)

    def begin_restore(self) -> bool:
        """Acquire the exclusive restore slot when no request is active."""
        with self._db_request_lock:
            if self._restore_in_progress or self._active_db_requests > 0:
                return False
            self._restore_in_progress = True
            return True

    def end_restore(self) -> None:
        with self._db_request_lock:
            self._restore_in_progress = False

    # --- job 缓存淘汰（调用方持?_jobs_lock?---
    def _prune_jobs_locked(self) -> None:
        """Documentation."""
        terminal = ("completed", "failed", "cancelled", "done", "succeeded")

        def _ts(job: JobStatus) -> float:
            ts = job.finished_at or job.started_at
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except Exception:  # noqa: BLE001 — finished_at 形态变化时保守跳过
                return time.time()

        now = time.time()
        to_remove: list[str] = []
        for jid, job in self.jobs.items():
            if job.status not in terminal:
                continue
            if (now - _ts(job)) > _JOB_TTL_SECONDS:
                to_remove.append(jid)
        # 数量上：淘汰最旧的终?job
        if len(self.jobs) - len(to_remove) > _JOB_MAX_KEEP:
            finished = sorted(
                (jid for jid, j in self.jobs.items() if j.status in terminal),
                key=lambda j: _ts(self.jobs[j]),
            )
            excess = len(self.jobs) - len(to_remove) - _JOB_MAX_KEEP
            if excess > 0:
                to_remove.extend(finished[:excess])
        if to_remove:
            for jid in to_remove:
                self.jobs.pop(jid, None)
                self.job_cancel_events.pop(jid, None)


# ============================================================
# HTTP 访问鉴权（本地服务令牌）
# ============================================================
# 本服务监听的 API 可以读写知识库、触发安装/重索引等敏感操作，且默认只绑
# 定 127.0.0.1。任何本机进程（含浏览器网页 JS，受 DNS rebinding 影响）都能
# 直接调用，所以默认启用 Bearer 令牌鉴权：
#   - 令牌文件：%LOCALAPPDATA%/doc2mind/server.token（与 server.port 同目录），
#     服务启动时幂等生成（存在则复用）；WPF 客户端启动后读取并注入请求头。
#   - 头格式：`Authorization: Bearer <token>` 或 `X-DocMind-Token: <token>`。
#   - `/v1/health` 保持匿名（健康探测/端口发现机制，不泄露敏感数据）。
# 开发/测试可用环境变量 `DOC2MIND_DISABLE_AUTH=1` 显式关闭（会打印警告）。
_TOKEN_FILE_NAME = "server.token"
_PUBLIC_PATHS = frozenset({"/v1/health"})


def _should_disable_auth() -> bool:
    """Documentation."""
    return os.environ.get("DOC2MIND_DISABLE_AUTH", "").strip().lower() in ("1", "true", "yes", "on")


def _load_or_create_auth_token(data_dir: Path) -> str | None:
    """Documentation."""
    token_file = data_dir / _TOKEN_FILE_NAME
    try:
        if token_file.is_file():
            tok = token_file.read_text(encoding="utf-8").strip()
            if tok and len(tok) >= 16:
                return tok
        tok = secrets.token_hex(32)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(tok + "\n", encoding="utf-8")
        with contextlib.suppress(OSError):
            token_file.chmod(0o600)  # POSIX 下收紧权限；Windows 由用户目录 ACL 保障
        return tok
    except OSError as e:
        logger.error(
            "Failed to load or create auth token %s: %s",
            token_file, e,
        )
        return None


def _build_auth_middleware(token: str):
    """Documentation."""
    async def _auth_middleware(request: Request, call_next: Any) -> Any:
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        provided = request.headers.get("Authorization", "")
        given = provided[7:].strip() if provided.startswith("Bearer ") else ""
        if not given:
            given = request.headers.get("X-DocMind-Token", "").strip()
        if not given or not secrets.compare_digest(given, token):
            return JSONResponse(
                status_code=401,
                content=ApiError(
                    code="UNAUTHORIZED",
                    message="Invalid or missing authorization token.",
                ).model_dump(),
            )
        return await call_next(request)

    return _auth_middleware


def create_app(settings: Settings | None = None) -> Any:
    """Documentation."""
    if FastAPI is Any:
        raise ImportError(
            "FastAPI Xip install doc2mind[server]"
        )

    # ?logs/doc2mind.log?stderr ?
    setup_logging()

    from doc2mind import __version__

    app = FastAPI(
        title="DocMind",
        description="Bt?HTTP API",
        version=__version__,
    )
    state = _AppState()
    if settings is not None:
        state.settings = settings
    app.state.doc2mind = state

    # --- earer kDOC2MIND_DISABLE_AUTH=1 ??---
    if _should_disable_auth():
        logger.warning("HTTP authentication is disabled via DOC2MIND_DISABLE_AUTH.")
    else:
        token = _load_or_create_auth_token(_user_data_dir())
        if token is not None:
            app.middleware("http")(_build_auth_middleware(token))
            logger.info("HTTP bearer authentication enabled; token file: %s", _user_data_dir() / _TOKEN_FILE_NAME)
        else:
            logger.warning("HTTP 鉴权降级为关闭：无法读写令牌文件 %s", _user_data_dir() / _TOKEN_FILE_NAME)

    # ---  JSON/SHN?10MB?---
    _MAX_BODY_BYTES = 10 * 1024 * 1024

    @app.middleware("http")
    async def _limit_body_size(request: Request, call_next: Any) -> Any:
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": f"Request body exceeds {_MAX_BODY_BYTES // (1024 * 1024)} MB.",
                    }
                },
            )
        return await call_next(request)

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
        # 数据库损?/ sqlite-vec 缺失时依然绿?"ok"，用?服务常但全部报??
        store_ok, store_error = True, None
        try:
            store = await asyncio.to_thread(state.ensure_open)
            if not await asyncio.to_thread(store.ping):
                store_ok = False
                store_error = "Vector store unavailable."
        except Exception as e:  # noqa: BLE001
            store_ok = False
            store_error = f"存储打开失败: {e}"
            logger.error("?s", e)

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

    # --- POST /v1/sample/ingestH?--
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

    # --- GET /v1/system/gpu-diagnosis?GPU ---
    @app.get("/v1/system/gpu-diagnosis", response_model=GpuDiagnosisResponse)
    async def gpu_diagnosis() -> GpuDiagnosisResponse:
        from doc2mind.core.system_env import get_gpu_diagnosis

        diagnosis = await asyncio.to_thread(get_gpu_diagnosis)
        return GpuDiagnosisResponse(**diagnosis)

    # --- POST /v1/system/install-gpu（一键装，SSE 流式日志?--
    @app.post("/v1/system/install-gpu")
    async def install_gpu(req: InstallGpuRequest) -> Any:
        from doc2mind.core.system_env import install_gpu_packages

        if req.path in _OCR_INSTALL_PATHS:
            # GPU X?CPU/OCR S
            # path="cpu" 静默装成 Paddle OCR）
            return StreamingResponse(
                _install_error_stream(
                    f"GPU installation cannot run while {req.path!r} is active. Use /v1/system/install-ocr.",
                ),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )
        if _backend_locks_heavy_runtime():
            return StreamingResponse(
                _install_error_stream(_HEAVY_RUNTIME_LOCKED_MESSAGE),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )

        async def event_generator() -> Any:
            async with _install_lock:
                try:
                    async for event in install_gpu_packages(req.path):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception as e:  # noqa: BLE001 — 异常以 SSE 错误帧结束
                    logger.error("GPU l?s", e)
                    yield (
                        "data: "
                        + json.dumps(
                            {"type": "error", "message": f"GPU installation failed: {e}"},
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                yield "data: [DONE]\n\n"

        # no-cache / X-Accel-Buffering?SSE ???
        # X?
        return StreamingResponse(event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS)

    # --- GET /v1/system/dependencies（设置页「环境自检」面板）---
    @app.get("/v1/system/dependencies")
    async def get_dependencies() -> dict[str, Any]:
        from doc2mind.core.system_env import get_dependencies_status

        return await asyncio.to_thread(get_dependencies_status)

    # --- GET /v1/system/local-ai-environment?AI ?--
    @app.get("/v1/system/local-ai-environment", response_model=LocalAiEnvironmentResponse)
    async def get_local_ai_environment_endpoint() -> LocalAiEnvironmentResponse:
        from doc2mind.core.local_ai_detect import get_local_ai_environment

        res = await get_local_ai_environment()
        return LocalAiEnvironmentResponse(**res)

    @app.post("/v1/system/ollama/pull")
    async def start_ollama_pull(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Start a single Ollama model pull and return its initial status."""
        model = str(body.get("model_name") or body.get("model") or "").strip()
        if not model:
            raise _api_error("BAD_REQUEST", "model_name is required.", 400)
        from doc2mind.core.ollama_pull import start_pull

        return await start_pull(model)

    @app.get("/v1/system/ollama/pull/status")
    async def get_ollama_pull_status() -> dict[str, Any]:
        """Return the current Ollama model pull status."""
        from doc2mind.core.ollama_pull import pull_status

        return await pull_status()

    # --- POST /v1/system/install-ocr（一键?OCRSE 流式日志?--
    @app.post("/v1/system/install-ocr")
    async def install_ocr(req: InstallOcrRequest) -> Any:
        from doc2mind.core.system_env import install_ocr_packages

        if req.path in _GPU_INSTALL_PATHS and req.path != "paddle-ocr-gpu":
            return StreamingResponse(
                _install_error_stream(
                    f"OCR installation cannot run while {req.path!r} is active. Use /v1/system/install-gpu.",
                ),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )
        if _backend_locks_heavy_runtime():
            return StreamingResponse(
                _install_error_stream(_HEAVY_RUNTIME_LOCKED_MESSAGE),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )

        async def event_generator() -> Any:
            async with _install_lock:
                try:
                    async for event in install_ocr_packages(req.path, force=req.force):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception as e:  # noqa: BLE001
                    logger.error("OCR l?s", e)
                    yield (
                        "data: "
                        + json.dumps(
                            {"type": "error", "message": f"OCR installation failed: {e}"},
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                yield "data: [DONE]\n\n"

        # no-cache / X-Accel-Buffering?SSE ???
        # X?
        return StreamingResponse(event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS)

    # --- POST /v1/system/download-model（嵌入模型下载，SSE 流式进?--
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
                    logger.error("下载失败: %s", e)
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

        # no-cache / X-Accel-Buffering?SSE ???
        # X?
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # --- POST /v1/system/download-ocr-models ---
    # 显式下载 PaddleOCR 官方模型到配置目录（国内源 modelscope/bos）。
    # 与嵌入模型 download-model 同为 SSE 进度流；设置页「下载 OCR 模型」调用。
    @app.post("/v1/system/download-ocr-models")
    async def download_ocr_models() -> Any:
        from doc2mind.core.loader.ocr_models import (
            download_ocr_models as _download,
        )
        from doc2mind.core.loader.ocr_models import (
            get_ocr_model_dir,
            ocr_model_cache_status,
        )

        async def event_generator() -> Any:
            loop = asyncio.get_running_loop()
            q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            def _on_progress(done: int, total: int, name: str) -> None:
                payload = {
                    "type": "progress",
                    "downloaded_files": done,
                    "total_files": total,
                    "model": name,
                    "ocr_model_dir": str(get_ocr_model_dir()),
                }
                try:
                    loop.call_soon_threadsafe(q.put_nowait, payload)
                except Exception:  # noqa: BLE001
                    pass

            def _do_download() -> None:
                try:
                    paths = _download(progress_cb=_on_progress)
                    loop.call_soon_threadsafe(
                        q.put_nowait,
                        {
                            "type": "done",
                            "path": str(get_ocr_model_dir()),
                            "models": paths,
                            "status": ocr_model_cache_status(),
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error("OCR 模型下载失败: %s", e)
                    loop.call_soon_threadsafe(
                        q.put_nowait,
                        {"type": "error", "message": str(e)},
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

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # --- GET /v1/jobs/{id}/events（job 进?SSE 实时流）---
    @app.get("/v1/jobs/{job_id}/events")
    async def job_events(job_id: str) -> Any:
        """Documentation."""
        # S?_prune_jobs_locked()?job ?SSE ?
        # 不应因淘汰其过?job 而返?404；测试也直接注入固?job_id 后阅?
        with state._jobs_lock:
            job = state.jobs.get(job_id)
        if job is None:
            raise _api_error("NOT_FOUND", f"任务不存在: {job_id}", 404)

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str] = asyncio.Queue()
        _subscribe_job(job_id, loop, q)

        async def event_stream() -> Any:
            try:
                # ?job ?
                with state._jobs_lock:
                    snap = {
                        "type": "progress",
                        "ts": _now_iso(),
                        "progress": job.progress,
                        "processed": job.processed,
                        "total": job.total,
                        "current_file": job.current_file,
                        "stage": job.stage,
                        "stage_progress": job.stage_progress,
                        "stage_detail": job.stage_detail,
                        "permission_request_id": job.permission_request_id,
                        "permission_tool_id": job.permission_tool_id,
                        "permission_timeout_sec": job.permission_timeout_sec,
                        "status": job.status,
                    }
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
                # b
                if job.status in ("completed", "failed", "cancelled", "done", "succeeded"):
                    yield f"data: {json.dumps({'type': job.status, 'ts': _now_iso()}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                while True:
                    try:
                        blob = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {blob}\n\n"
                        # 解析判断终?
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

    # --- GET/POST /v1/config?+ ?+ ?+ LLM?--
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
            rag_mode=getattr(s, "rag_mode", "hybrid"),
            rag_system_prompt=s.rag_system_prompt,
            rag_max_history_tokens=getattr(s, 'rag_max_history_tokens', 4096),
            rerank_enabled=bool(s.rerank_enabled),
            rerank_model=s.rerank_model,
            rerank_recall=int(s.rerank_recall),
            llm_timeout=s.llm_timeout,
            web_search_timeout=getattr(s, "web_search_timeout", 36.0),
            search_provider=str(getattr(s, "search_provider", "builtin") or "builtin"),
            search_provider_api_key_configured=bool(getattr(s, "search_provider_api_key", None)),
            search_provider_endpoint=getattr(s, "search_provider_endpoint", None) or None,
            citation_min_score=float(getattr(s, "citation_min_score", 0.45) or 0.45),
            citation_bg_score_ratio=float(getattr(s, "citation_bg_score_ratio", 0.6) or 0.6),
            background_hit_limit=int(getattr(s, "background_hit_limit", 5) or 0),
            stage_elapsed_enabled=bool(getattr(s, "stage_elapsed_enabled", True)),
            llm_first_token_slow_ms=int(getattr(s, "llm_first_token_slow_ms", 30000) or 30000),
            watch_paths=list(s.watch_paths),
            watch_debounce_seconds=s.watch_debounce_seconds,
            llm_api_key_configured=bool(s.llm_api_key),
            config_error=get_config_load_error(),
            # Agent 升级预（旧前略即可?
            agent_mode_enabled=bool(getattr(s, "agent_mode_enabled", False)),
            agent_file_write_policy=str(getattr(s, "agent_file_write_policy", "session_allow") or "session_allow"),
            agent_native_tool_calling=bool(getattr(s, "agent_native_tool_calling", True)),
            chat_mode_default=str(getattr(s, "chat_mode_default", "rag") or "rag"),
            chat_mode_auto_enabled=bool(getattr(s, "chat_mode_auto_enabled", True)),
            auto_curate_on_ingest=bool(getattr(s, "auto_curate_on_ingest", False)),
            mcp_client_enabled=bool(getattr(s, "mcp_client_enabled", False)),
            mcp_servers_configured=bool(str(getattr(s, "mcp_servers_json", "") or "").strip() not in ("", "[]")),
            longform_enabled=bool(getattr(s, "longform_enabled", False)),
            longform_max_sections=int(getattr(s, "longform_max_sections", 8) or 8),
            longform_max_chars=int(getattr(s, "longform_max_chars", 12000) or 12000),
            thinking_meta_filter_enabled=bool(getattr(s, "thinking_meta_filter_enabled", True)),
        )

    @app.post("/v1/config", response_model=ConfigResponse)
    async def update_config(req: ConfigUpdate) -> ConfigResponse:
        s = state.settings
        old_model = s.embed_model

        # provider ?00?
        if req.llm_provider is not None:
            provider = req.llm_provider.strip()
            if provider not in SUPPORTED_PROVIDERS:
                raise _api_error(
                    "BAD_REQUEST",
                    f"y llm_provider: {provider}? {'/'.join(SUPPORTED_PROVIDERS)}",
                    400,
                )

        # 目标嵌入器：按请求模型或当前最新 settings 创建并探测维度
        updates = req.model_dump(exclude_none=True, exclude={"persist"})
        # 空字符串 = 显式清除 llm_api_key / llm_base_url / llm_model / rag_system_prompt
        # （exclude_none 会忽?null，前空r时?""?
        for k in (
            "embed_model_path",
            "llm_api_key",
            "llm_base_url",
            "llm_model",
            "rag_system_prompt",
        ):
            if k in updates and isinstance(updates[k], str) and not updates[k].strip():
                updates[k] = None
        embedder_config_changed = any(
            key in updates for key in ("embed_model", "embed_model_path")
        )
        if updates:
            for k, v in updates.items():
                if hasattr(s, k):
                    setattr(s, k, v)
            # u Xenova/bge-reranker-v2-m3?
            # r前旧默认次推回后每轮「索降级?
            if "rerank_model" in updates and hasattr(s, "_sanitize_rerank_model"):
                s._sanitize_rerank_model()
            if "citation_min_score" in updates and hasattr(s, "_sanitize_citation_gate"):
                s._sanitize_citation_gate()
            if embedder_config_changed:
                state.embedder = None

        # ?search_provider_api_key = 显式清除（回?builtin?
        if "search_provider_api_key" in req.model_dump(exclude_none=False) and req.search_provider_api_key == "":
            s.search_provider_api_key = None

        # 模型切换引导：维度变化时提示用户重建索引，并同步 settings.embed_dim
        # ? store ?
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
                    "已切换嵌入模型，旧索引与新模型不匹配。"
                    "请到「文档库」点「重建索引」（在此之前关键词检索仍可用）。"
                )

        # 持久化到 config.toml（下次启动自动生效）；persist=False 时只改运行?
        # a?
        # 失败不静默——写入 notice 提示用户本次已生效但重启后可能回退
        if req.persist:
            try:
                from doc2mind.core.config import save_settings

                if not save_settings(s):
                    msg = "Settings could not be saved to config.toml."
                    notice = f"{notice} {msg}" if notice else msg
            except Exception as e:  # noqa: BLE001 — 持久化失败不影响本次运行时更新
                    msg = f"Settings updated in memory but could not be saved: {e}"
                    notice = f"{notice} {msg}" if notice else msg

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
            rag_mode=getattr(s, "rag_mode", "hybrid"),
            rag_system_prompt=s.rag_system_prompt,
            rag_max_history_tokens=getattr(s, 'rag_max_history_tokens', 4096),
            rerank_enabled=bool(s.rerank_enabled),
            rerank_model=s.rerank_model,
            rerank_recall=int(s.rerank_recall),
            llm_timeout=s.llm_timeout,
            web_search_timeout=getattr(s, "web_search_timeout", 36.0),
            search_provider=str(getattr(s, "search_provider", "builtin") or "builtin"),
            search_provider_api_key_configured=bool(getattr(s, "search_provider_api_key", None)),
            search_provider_endpoint=getattr(s, "search_provider_endpoint", None) or None,
            citation_min_score=float(getattr(s, "citation_min_score", 0.45) or 0.45),
            citation_bg_score_ratio=float(getattr(s, "citation_bg_score_ratio", 0.6) or 0.6),
            background_hit_limit=int(getattr(s, "background_hit_limit", 5) or 0),
            stage_elapsed_enabled=bool(getattr(s, "stage_elapsed_enabled", True)),
            llm_first_token_slow_ms=int(getattr(s, "llm_first_token_slow_ms", 30000) or 30000),
            watch_paths=list(s.watch_paths),
            watch_debounce_seconds=s.watch_debounce_seconds,
            llm_api_key_configured=bool(s.llm_api_key),
            notice=notice,
            agent_mode_enabled=bool(getattr(s, "agent_mode_enabled", False)),
            agent_file_write_policy=str(getattr(s, "agent_file_write_policy", "session_allow") or "session_allow"),
            agent_native_tool_calling=bool(getattr(s, "agent_native_tool_calling", True)),
            chat_mode_default=str(getattr(s, "chat_mode_default", "rag") or "rag"),
            chat_mode_auto_enabled=bool(getattr(s, "chat_mode_auto_enabled", True)),
            auto_curate_on_ingest=bool(getattr(s, "auto_curate_on_ingest", False)),
            mcp_client_enabled=bool(getattr(s, "mcp_client_enabled", False)),
            mcp_servers_configured=bool(str(getattr(s, "mcp_servers_json", "") or "").strip() not in ("", "[]")),
            longform_enabled=bool(getattr(s, "longform_enabled", False)),
            longform_max_sections=int(getattr(s, "longform_max_sections", 8) or 8),
            longform_max_chars=int(getattr(s, "longform_max_chars", 12000) or 12000),
            thinking_meta_filter_enabled=bool(getattr(s, "thinking_meta_filter_enabled", True)),
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

        # ?Settingsdataclasses.replace 
        tmp = dataclasses.replace(
            s,
            llm_provider=provider,
            llm_api_key=(req.api_key or "").strip() or s.llm_api_key,
            llm_base_url=(req.base_url or "").strip() or s.llm_base_url,
            llm_model=(req.model or "").strip() or s.llm_model,
        )

        def _run() -> tuple[str, str, str]:
            """Documentation."""
            client = get_llm_client(tmp)
            if client is None:  # pragma: no cover — provider 已校验，防御分支
                raise LLMError("LLM client unavailable")
            if req.stream:
                # 流式探测：逐 token 收集到 16 个字符即停（验证 SSE 链路即可，
                # 不追求完整回复）
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
                reply_preview=(reply or "")[:100] or "No reply",
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
                error=f"LLM request timed out after {req.timeout:.0f}s.",
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
                raise LLMError("LLM client unavailable")
            return client.provider, client.list_models(timeout=req.timeout)

        try:
            provider_used, models = await asyncio.to_thread(_run)
            from doc2mind.core.llm.model_registry import get_model_spec

            meta: dict[str, LlmModelMetaDTO] = {}
            for m in models:
                spec = get_model_spec(m, provider_used)
                meta[m] = LlmModelMetaDTO(
                    context_window=spec.context_window,
                    max_output_tokens=spec.max_output_tokens,
                    is_reasoning_model=spec.is_reasoning_model,
                    display_name=spec.display_name,
                    summary_text=spec.summary_text,
                )
            return LlmModelsResponse(ok=True, provider=provider_used, models=models, model_meta=meta)
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
            raise _api_error("BAD_REQUEST", f"S? {req.path}", 400)

        # collection 容错：None/??"default"
        collection = req.collection.strip() if req.collection else "default"
        if not collection:
            collection = "default"

        # 复?_AppState 的单?store，避免每次求新?sqlite 连?
        # 触发 WAL 锁冲突。嵌入是 CPU 密集，用线程避免阻塞事件循环。
        store = await asyncio.to_thread(state.ensure_open)

        def _do_ingest():
            # 写互斥：与 delete / reindex 串行，避免并发写冲突
            with state._write_lock:
                return ingest_path(
                    path=p,
                    collection=collection,
                    recursive=req.recursive,
                    force=req.force,
                    store=store,
                    cancel_event=None,
                )

        summary = await asyncio.to_thread(_do_ingest)
        # 后台 AI 整理：写锁已释放，整理剥离到独立 job，导入响应不再等 LLM
        _spawn_background_curate(state, summary.curatable_document_ids, collection)
        return IngestResponse(
            ingested=[
                IngestResultDTO(**r.__dict__) for r in summary.results
                if r.status == "ingested"
            ],
            skipped=summary.skipped,
            failed=summary.failed,
            total_documents=summary.total_documents,
            total_chunks=summary.total_chunks,
            long_chunk_count=summary.long_chunk_count,
            health_warnings=list(summary.health_warnings),
            suggest_query=summary.suggest_query,
            failed_details=[
                IngestResultDTO(**r.__dict__) for r in summary.results
                if r.status == "failed"
            ],
        )

    # --- POST /v1/ingest/textAI o/?---
    @app.post("/v1/ingest/text", response_model=IngestResponse)
    async def ingest_text_endpoint(req: IngestTextRequest) -> IngestResponse:
        if not req.text or not req.text.strip():
            raise _api_error("BAD_REQUEST", "text is required.", 400)

        # collection=None 透传：落默认集合并允许 AI 自动归类（显式传值则尊重选择）
        store = await asyncio.to_thread(state.ensure_open)
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

    # --- POST /v1/collections（创空t库集合?---
    @app.post("/v1/collections", response_model=StatsResponse)
    async def create_collection(req: CreateCollectionRequest) -> StatsResponse:
        name = (req.name or "").strip()
        if not name:
            raise _api_error("BAD_REQUEST", "Collection name is required.", 400)
        # ?????SQL ?
        if not all(c.isalnum() or c in "_- " or ("\u4e00" <= c <= "\u9fff") for c in name):
            raise _api_error(
                "BAD_REQUEST",
                "Collection name contains invalid characters.",
                400,
            )
        name = name.replace(" ", "_")

        store = await asyncio.to_thread(state.ensure_open)
        await asyncio.to_thread(store.ensure_collection, name)
        stats = await asyncio.to_thread(store.get_stats)
        return StatsResponse(
            total_documents=stats.total_documents,
            total_chunks=stats.total_chunks,
            collections={k: list(v) for k, v in stats.collections.items()},
        )

    @app.get("/v1/stats", response_model=StatsResponse)
    async def get_stats(collection: str | None = Query(None)) -> StatsResponse:
        """Return library counts and per-collection document/chunk/size totals."""
        store = await asyncio.to_thread(state.ensure_open)
        stats = await asyncio.to_thread(store.get_stats)
        collections = stats.collections
        if collection:
            selected = collections.get(collection)
            collections = {collection: selected} if selected is not None else {}
            total_documents = selected[0] if selected else 0
            total_chunks = selected[1] if selected else 0
        else:
            total_documents = stats.total_documents
            total_chunks = stats.total_chunks
        return StatsResponse(
            total_documents=total_documents,
            total_chunks=total_chunks,
            collections={name: list(values) for name, values in collections.items()},
        )

    @app.get("/v1/quality", response_model=QualityResponse)
    async def get_quality(collection: str | None = Query(None)) -> QualityResponse:
        """Return document format distribution and actionable quality warnings."""
        store = await asyncio.to_thread(state.ensure_open)
        docs = await asyncio.to_thread(
            store.list_documents, collection=collection, limit=10000
        )
        format_distribution: dict[str, int] = {}
        total_chunks = 0
        warnings: list[str] = []
        for doc in docs:
            format_distribution[doc.format] = format_distribution.get(doc.format, 0) + 1
            total_chunks += doc.chunk_count
            if doc.chunk_count == 0:
                warnings.append(f"文档「{doc.source}」分块数为 0，可能无法被检索到")
            if doc.size_bytes > 50 * 1024 * 1024:
                warnings.append(
                    f"文档「{doc.source}」体积较大 "
                    f"({doc.size_bytes / (1024 * 1024):.1f} MB)，建议拆分后导入"
                )
        return QualityResponse(
            collection=collection,
            total_documents=len(docs),
            total_chunks=total_chunks,
            format_distribution=format_distribution,
            warnings=warnings[:20],
        )

    @app.get("/v1/library/status")
    async def get_library_status_endpoint() -> dict[str, Any]:
        """Return whether the active library is empty, healthy, or needs reindexing."""
        from doc2mind.core.library_status import get_library_status

        store = await asyncio.to_thread(state.ensure_open)
        return await asyncio.to_thread(get_library_status, store, state.settings)

    @app.post("/v1/profile")
    async def set_usage_profile(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Apply and persist one of the supported usage profiles."""
        from doc2mind.core.config import USAGE_PROFILES, apply_usage_profile, save_settings

        profile = str(body.get("profile") or "").strip().lower()
        preset = USAGE_PROFILES.get(profile)
        if preset is None:
            raise _api_error("BAD_REQUEST", f"Unknown usage profile: {profile}", 400)
        updated = apply_usage_profile(state.settings, profile)
        if not save_settings(updated):
            raise _api_error("CONFIG_ERROR", "Failed to persist usage profile.", 500)
        state.settings = updated
        return {
            "profile": profile,
            "preset": {
                "label": preset["label"],
                "description": preset["description"],
            },
            "applied": {
                "rag_top_k": updated.rag_top_k,
                "rag_min_score": updated.rag_min_score,
                "rag_mode": updated.rag_mode,
                "query_expansion": updated.query_expansion,
            },
        }

    @app.post("/v1/backup", response_model=BackupResponse)
    async def create_backup_endpoint(req: BackupCreateRequest) -> BackupResponse:
        """Create a consistent SQLite backup archive."""
        if not state.begin_db_request():
            raise _api_error("BUSY", "知识库正在恢复，请稍后重试。", 409)
        try:
            output = (
                Path(req.output_path).expanduser()
                if req.output_path and req.output_path.strip()
                else _user_data_dir() / "backups" / f"docmind-{datetime.now():%Y%m%d-%H%M%S}.zip"
            )
            manifest = await asyncio.to_thread(create_backup, state.settings.db_path, output)
            return BackupResponse(
                ok=True,
                path=str(manifest.get("path", output)),
                created_at=manifest.get("created_at"),
                database_size=int(manifest.get("database_size", 0) or 0),
                tables=list(manifest.get("tables", [])),
            )
        except BackupError as exc:
            raise _api_error("BACKUP_ERROR", str(exc), 400) from exc
        finally:
            state.end_db_request()

    @app.post("/v1/backup/restore", response_model=BackupResponse)
    async def restore_backup_endpoint(req: BackupRestoreRequest) -> BackupResponse:
        """Restore a validated backup, preserving a pre-restore database copy."""
        if not state.begin_restore():
            raise _api_error("BUSY", "知识库当前有活动请求，无法恢复备份。", 409)
        try:
            if state.store is not None:
                await asyncio.to_thread(state.store.close)
                state.store = None
            result = await asyncio.to_thread(
                restore_backup, state.settings.db_path, Path(req.backup_path).expanduser()
            )
            manifest = result.get("manifest") or {}
            return BackupResponse(
                ok=True,
                path=str(result.get("path", state.settings.db_path)),
                created_at=manifest.get("created_at"),
                database_size=int(manifest.get("database_size", 0) or 0),
                tables=list(manifest.get("tables", [])),
                previous_backup=result.get("previous_backup"),
            )
        except BackupError as exc:
            raise _api_error("RESTORE_ERROR", str(exc), 400) from exc
        finally:
            state.end_restore()

    @app.post("/v1/diagnostics/bundle", response_model=BackupResponse)
    async def create_diagnostics_bundle(req: DiagnosticBundleRequest) -> BackupResponse:
        """Create a diagnostics archive without including the knowledge-base database."""
        if not state.begin_db_request():
            raise _api_error("BUSY", "知识库正在恢复，请稍后重试。", 409)
        try:
            output = (
                Path(req.output_path).expanduser()
                if req.output_path and req.output_path.strip()
                else _user_data_dir() / "diagnostics" / f"docmind-diagnostics-{datetime.now():%Y%m%d-%H%M%S}.zip"
            )
            report = await asyncio.to_thread(
                lambda: __import__("doc2mind.core.doctor", fromlist=["run_diagnostics"])
                .run_diagnostics(check_network=False)
                .to_dict()
            )
            runtime = {
                "format": "docmind-diagnostics",
                "created_at": _now_iso(),
                "platform": platform.platform(),
                "python_version": sys.version,
                "backend_version": __version__,
                "include_logs": bool(req.include_logs),
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("doctor.json", json.dumps(report, ensure_ascii=False, indent=2))
                archive.writestr("runtime.json", json.dumps(runtime, ensure_ascii=False, indent=2))
            return BackupResponse(ok=True, path=str(output), database_size=output.stat().st_size)
        except OSError as exc:
            raise _api_error("DIAGNOSTICS_ERROR", f"无法写入诊断包: {exc}", 400) from exc
        finally:
            state.end_db_request()

    # --- POST /v1/ingest/job（异摄入：大目录不请求，返?job_id 进?---
    @app.post("/v1/ingest/job", response_model=JobStatus)
    async def ingest_job(req: IngestRequest) -> JobStatus:
        p = Path(req.path)
        if not p.exists():
            raise _api_error("BAD_REQUEST", f"S? {req.path}", 400)

        collection = req.collection.strip() if req.collection else "default"
        if not collection:
            collection = "default"

        store = await asyncio.to_thread(state.ensure_open)
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
            state.job_cancel_events[job_id] = threading.Event()

        cancel_event = state.job_cancel_events[job_id]

        # FC-01b?
        partial_lock = threading.Lock()
        partial_results: list[IngestResultDTO] = []

        def _on_file_result(res: Any) -> None:
            dto = IngestResultDTO(
                source=getattr(res, "source", ""),
                collection=getattr(res, "collection", collection),
                format=getattr(res, "format", "unknown"),
                size_bytes=int(getattr(res, "size_bytes", 0) or 0),
                chunk_count=int(getattr(res, "chunk_count", 0) or 0),
                elapsed_ms=int(getattr(res, "elapsed_ms", 0) or 0),
                status=getattr(res, "status", "unknown"),
                error=getattr(res, "error", None),
                document_id=getattr(res, "document_id", None),
            )
            with partial_lock:
                partial_results.append(dto)

        def _fill_cancelled_job(job_obj: JobStatus) -> str:
            """Documentation."""
            with partial_lock:
                snapshot = list(partial_results)
            job_obj.results = snapshot
            n_ingested = sum(1 for r in snapshot if r.status == "ingested")
            n_skipped = sum(1 for r in snapshot if r.status == "skipped")
            n_failed = sum(1 for r in snapshot if r.status == "failed")
            job_obj.processed = n_ingested + n_skipped + n_failed
            job_obj.current_file = None
            note = f"Cancelled after processing {n_ingested} files."
            if n_skipped:
                note += f"，跳过 {n_skipped}"
            if n_failed:
                note += f"，失败 {n_failed}"
            note += " Cancellation requested by user."
            job_obj.cancel_note = note
            return note

        def _check_and_update_progress(
            done: int,
            total: int,
            current_file: str | None = None,
            stage: str | None = None,
            stage_progress: float | None = None,
            stage_detail: str | None = None,
        ) -> None:
            if cancel_event.is_set():
                raise _JobCancelledError("Job cancelled")
            with state._jobs_lock:
                if job.status == "cancelled":
                    raise _JobCancelledError("Job cancelled")
            _update_ingest_job(state, job, done, total,
                               current_file=current_file, stage=stage,
                               stage_progress=stage_progress,
                               stage_detail=stage_detail)

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
                        cancel_event=cancel_event,
                        on_result=_on_file_result,
                    )
                with state._jobs_lock:
                    if job.status == "cancelled":
                        note = _fill_cancelled_job(job)
                        logger.info("? %s %s", job_id, note)
                        job.finished_at = _now_iso()
                        _broadcast_job_event(job_id, {
                            "type": "cancelled", "ts": _now_iso(),
                            "cancel_note": note,
                            "processed": job.processed,
                            "results_count": len(job.results),
                        })
                        return
                    job.status = "completed"
                    job.progress = 1.0
                    job.processed = summary.total_documents + summary.skipped + summary.failed
                    job.finished_at = _now_iso()
                    job.current_file = None
                    job.cancel_note = None
                    job.results = [
                        IngestResultDTO(**r.__dict__) for r in summary.results
                    ]
                curate_job_id = _spawn_background_curate(
                    state, summary.curatable_document_ids, collection
                )
                _broadcast_job_event(
                    job_id,
                    {"type": "done", "ts": _now_iso(), "status": "completed",
                     "processed": job.processed,
                     "curate_job_id": curate_job_id,
                     "curate_total": len(summary.curatable_document_ids)},
                )
            except Exception as e:  # noqa: BLE001
                with state._jobs_lock:
                    is_cancel = (
                        job.status == "cancelled"
                        or isinstance(e, _JobCancelledError)
                        or "已取消" in str(e)
                    )
                    if is_cancel:
                        job.status = "cancelled"
                        note = _fill_cancelled_job(job)
                        job.finished_at = _now_iso()
                        logger.info("任务取消: %s %s", job_id, note)
                        _broadcast_job_event(job_id, {
                            "type": "cancelled", "ts": _now_iso(),
                            "cancel_note": note,
                            "processed": job.processed,
                            "results_count": len(job.results),
                        })
                        return
                    job.status = "failed"
                    job.error = str(e)
                    # 失败时也尽量带上已成功细，便于u排查X
                    with partial_lock:
                        if partial_results and not job.results:
                            job.results = list(partial_results)
                    job.finished_at = _now_iso()
                    _broadcast_job_event(job_id, {"type": "failed", "ts": _now_iso(), "error": str(e)})

        threading.Thread(target=_run_ingest_job, daemon=True).start()
        return job

    # --- POST /v1/search ---
    @app.post("/v1/search", response_model=SearchResponse)
    async def search(req: SearchRequest) -> SearchResponse:
        store = await asyncio.to_thread(state.ensure_open)
        assert state.embedder is not None
        try:
            _s = get_settings()
            reranker_settings = _s
            if req.rerank is True and not _s.rerank_enabled:
                reranker_settings = dataclasses.replace(_s, rerank_enabled=True)
            retriever = Retriever(
                store=store,
                embedder=state.embedder,
                reranker=(
                    None if req.rerank is False else get_reranker(reranker_settings)
                ),
                rerank_recall=_s.rerank_recall,
                query_instruction=_s.query_instruction,
                semantic_floor=_s.semantic_floor,
                rrf_weights=parse_rrf_weights(_s.rrf_weights),
                fusion_mode=_s.fusion_mode,
                rerank_calibration_temperature=_s.rerank_calibration_temperature,
            )
            hits, stats = await asyncio.to_thread(
                retriever.search,
                req.query,
                req.collection,  # None = 跨全部集合
                req.top_k,
                req.min_score or 0.0,  # Optional → float
            )
        except Exception as e:  # noqa: BLE001
            raise _api_error("INTERNAL", f"检索失败: {e}", 500) from e

        # a?/ ?/ g?
        # u?h?
        message: str | None = getattr(stats, "message", None)
        if message is None:
            if not hits:
                try:
                    total_docs = await asyncio.to_thread(
                        store.count_documents, None, None
                    )
                except Exception:  # noqa: BLE001
                    total_docs = -1
                if total_docs == 0:
                    message = "The knowledge base is empty."
                elif req.collection and await asyncio.to_thread(
                    store.count_documents, req.collection, None
                ) == 0:
                    message = f"No documents found in collection {req.collection!r}."
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
                    rerank_score=round(h.rerank_score, 4) if h.rerank_score is not None else None,
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
        """Documentation."""
        store = await asyncio.to_thread(state.ensure_open)
        try:
            # 按请求携带的服务商配置构造临时 LLM 客户端（复用 /v1/llm/test 模式）
            llm_client = _build_llm_client_from_provider(req, state.settings)
            answer = await asyncio.to_thread(
                rag_answer,
                req.query,
                req.collection,
                req.top_k,
                req.chat_id,
                collections=req.collections,
                model_override=req.model,
                llm_client=llm_client,
                enable_web_search=req.enable_web_search,
                web_search_mode=req.resolved_web_search_mode(),
                entity_context=req.entity_context,
                persona=req.persona,
                persona_prompt=req.persona_prompt,
                store=store,
                embedder=state.embedder,
                attachments=req.attachments,
                github_token=req.github_token,
                rag_mode=req.rag_mode,
                memory_context=req.memory_context,
                task_context=req.task_context,
            )
        except RagError as e:
            raise _api_error("RAG_ERROR", str(e), 400) from e
        except LLMError as e:
            raise _api_error("RAG_ERROR", f"LLM 配置错误: {e}", 400) from e
        except Exception as e:  # noqa: BLE001
            raise _api_error("INTERNAL", f"检索失败: {e}", 500) from e

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
                    score_type=getattr(s, "score_type", ""),
                    confidence_label=getattr(s, "confidence_label", ""),
                    source_type=getattr(s, "source_type", "local"),
                    url=getattr(s, "url", None),
                    title=getattr(s, "title", None),
                    snippet=getattr(s, "snippet", None),
                    source_name=getattr(s, "source_name", None),
                    domain=getattr(s, "domain", None),
                    published_at=getattr(s, "published_at", None),
                    content_fetched=getattr(s, "content_fetched", False),
                    corroborated_by=getattr(s, "corroborated_by", 0),
                    evidence_level=getattr(s, "evidence_level", "unknown"),
                )
                for s in answer.sources
            ],
            evidence=(
                EvidenceSummaryDTO(**answer.evidence)
                if getattr(answer, "evidence", None)
                else None
            ),
            timing=dict(getattr(answer, "timing", None) or {}),
        )

    # --- POST /v1/chat/stream (SSE) ---
    # SSE 敛护xAUD-005 / AUD-006?
    # - ?maxsize put j
    # - qb
    _SSE_QUEUE_MAXSIZE = 64
    _SSE_PUT_TIMEOUT = 5.0
    _SSE_MAX_STREAM_SECONDS = 600.0  # M?0 ?
    _SSE_MAX_IDLE_HEARTBEATS = 20  # q?0?5s ?5 ?token?

    @app.post("/v1/chat/stream")
    async def chat_stream(req: ChatRequest) -> Any:
        """Documentation."""
        async def event_generator() -> Any:
            loop = asyncio.get_event_loop()
            # maxsize LLM ?
            # put Q?AUD-005?
            queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
            store = await asyncio.to_thread(state.ensure_open)
            stop_event = threading.Event()
            # ?LLM g?
            # ?Noneq
            try:
                llm_client = _build_llm_client_from_provider(req, state.settings)
            except LLMError as e:
                if not stop_event.is_set():
                    yield f"data: {json.dumps({'error': f'LLM 配置错误: {e}'}, ensure_ascii=False)}\n\n"
                return
            #  + ChatMode ai-chat-architecture-chat-mode.md?
            from doc2mind.core.agent.chat_mode import (
                decision_status_message,
                resolve_chat_mode,
            )

            agent_allowed = bool(getattr(state.settings, "agent_mode_enabled", False))
            decision = resolve_chat_mode(
                req.requested_chat_mode(),
                agent_flag=bool(req.agent_mode),
                legacy_mode=req.mode,
                agent_allowed=agent_allowed,
                default_mode=str(getattr(state.settings, "chat_mode_default", "rag") or "rag"),
                query=req.query or "",
                enable_web_search=bool(req.enable_web_search),
                web_search_mode=req.resolved_web_search_mode(),
                continue_writing=bool(req.continue_writing),
                auto_enabled=bool(getattr(state.settings, "chat_mode_auto_enabled", True)),
            )
            mode_msg = decision_status_message(decision)
            if not stop_event.is_set():
                yield f"data: {json.dumps({'type': 'status', 'message': mode_msg}, ensure_ascii=False)}\n\n"
            if decision.degraded:
                logger.warning("ChatMode 降级: requested=%s → %s (%s)", decision.requested, decision.mode, decision.reason)

            if decision.mode == "agent":
                from doc2mind.core.agent.runtime.chat_agent import agent_answer_stream

                gen = agent_answer_stream(
                    req.query,
                    collection=req.collection,
                    top_k=req.top_k,
                    chat_id=req.chat_id,
                    settings=state.settings,
                    llm_client=llm_client,
                    collections=req.collections,
                    model_override=req.model,
                    enable_web_search=req.enable_web_search,
                    web_search_mode=req.resolved_web_search_mode(),
                    github_token=req.github_token,
                    store=store,
                    embedder=state.embedder,
                    stop_event=stop_event,
                    persona=req.persona,
                    persona_prompt=req.persona_prompt,
                    attachments=req.attachments,
                    memory_context=req.memory_context,
                    task_context=req.task_context,
                    response_mode=req.response_mode,
                    answer_format=req.answer_format,
                )
            else:
                gen = rag_answer_stream(
                    req.query, req.collection, req.top_k, req.chat_id,
                    collections=req.collections,
                    model_override=req.model,
                    llm_client=llm_client,
                    enable_web_search=req.enable_web_search,
                    web_search_mode=req.resolved_web_search_mode(),
                    entity_context=req.entity_context,
                    persona=req.persona,
                    persona_prompt=req.persona_prompt,
                    store=store,
                    embedder=state.embedder,
                    stop_event=stop_event,
                    attachments=req.attachments,
                    github_token=req.github_token,
                    rag_mode=req.rag_mode,
                    memory_context=req.memory_context,
                    task_context=req.task_context,
                    response_mode=req.response_mode,
                    answer_format=req.answer_format,
                    continue_writing=req.continue_writing,
                )

            _chat_mode_meta = {
                "chat_mode": decision.mode,
                "chat_mode_requested": decision.requested,
                "chat_mode_reason": decision.reason,
                "chat_mode_degraded": decision.degraded,
            }

            def _enrich_done(chunk: str | None) -> str | None:
                """Documentation."""
                if not chunk:
                    return chunk
                prefix = "data: "
                raw = chunk[len(prefix):] if chunk.startswith(prefix) else chunk
                raw = raw.strip()
                if not raw or not raw.startswith("{"):
                    return chunk
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    return chunk
                if not isinstance(obj, dict) or not obj.get("done"):
                    return chunk
                obj.update(_chat_mode_meta)
                #  JSONevent_generator?
                # "data: {chunk}\n\n"。再?"data: " 前缀，SSE 行会变成
                # "data: data: {...}"?'d' ?
                # JSON ?PARSE_ERRORV?
                return json.dumps(obj, ensure_ascii=False)

            def _push(chunk: str | None) -> None:
                """Documentation."""
                if stop_event.is_set():
                    return
                try:
                    asyncio.run_coroutine_threadsafe(
                        queue.put(chunk), loop
                    ).result(timeout=_SSE_PUT_TIMEOUT)
                except Exception as e:  # noqa: BLE001
                    # ?/ put ?
                    logger.warning("SSE f?? %s", e)
                    stop_event.set()

            def _pump() -> None:
                try:
                    for chunk_json in gen:
                        if stop_event.is_set():
                            break
                        _push(_enrich_done(chunk_json))
                except RagError as e:
                    _push(f"__ERROR__:{e}")
                except Exception as e:  # noqa: BLE001
                    _push(f"__ERROR__:对话失败: {e}")
                finally:
                    #  RAG ??GeneratorExit ?
                    # S?GC
                    with contextlib.suppress(Exception):
                        gen.close()
                    _push(None)

            fut = loop.run_in_executor(None, _pump)
            started_at = time.monotonic()
            idle_heartbeats = 0
            try:
                while True:
                    # jb?LLM AUD-006?
                    if time.monotonic() - started_at > _SSE_MAX_STREAM_SECONDS:
                        yield f"data: {json.dumps({'error': 'Stream exceeded maximum duration.'}, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    try:
                        chunk = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        idle_heartbeats += 1
                        # q?token?
                        if idle_heartbeats >= _SSE_MAX_IDLE_HEARTBEATS:
                            yield f"data: {json.dumps({'error': '模型生成超时（长时间无输出），已自动终止'}, ensure_ascii=False)}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                        # LLM ?token ?A?15s?
                        # 间无数据会代理/防墙静掐?SSE 连接?
                        # ?SSE r?
                        yield ": heartbeat\n\n"
                        continue
                    idle_heartbeats = 0
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

        # no-cache / X-Accel-Buffering?SSE ???
        # X?
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # --- GET /v1/chatsq= ??---
    @app.get("/v1/chats", response_model=ChatListResponse)
    async def list_chats(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        q: str | None = Query(None, min_length=1, description="?"),
    ) -> ChatListResponse:
        store = ChatStore(state.settings.db_path)
        try:
            sessions, total = await asyncio.to_thread(
                lambda: (store.list_sessions(limit, offset, q=q), store.count_sessions(q=q))
            )
        except ChatStoreError as e:
            raise _api_error("INTERNAL", f"切换会话失败: {e}", 500) from e
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
            # AUD-013otal q `
            total=total,
        )

    # --- GET /v1/chats/{chat_id}X??---
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
            raise _api_error("INTERNAL", f"? {e}", 500) from e
        if summary is None:
            raise _api_error("NOT_FOUND", f"会话不存在:  {chat_id}", 404)

        parsed_messages = []
        for m in messages:
            srcs: list[SourceRefDTO] = []
            trajectory: list[dict[str, Any]] = []
            trajectory_warning: str | None = None
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
                                score_type=s.get("score_type", ""),
                                confidence_label=s.get("confidence_label", ""),
                                source_type=s.get("source_type", "local"),
                                url=s.get("url"),
                                title=s.get("title"),
                                snippet=s.get("snippet"),
                                source_name=s.get("source_name"),
                                domain=s.get("domain"),
                                published_at=s.get("published_at"),
                                content_fetched=s.get("content_fetched", False),
                                corroborated_by=s.get("corroborated_by", 0),
                                evidence_level=s.get("evidence_level", "unknown"),
                            )
                            for idx, s in enumerate(raw_srcs)
                        ]
                except Exception:  # noqa: BLE001
                    pass
            if getattr(m, "trajectory_json", None):
                try:
                    raw_trajectory = json.loads(m.trajectory_json)
                    # 兼容原先仅保存步骤列表的会话，以及带恢复提示的新对象形态。
                    if isinstance(raw_trajectory, list):
                        trajectory = [step for step in raw_trajectory if isinstance(step, dict)]
                    elif isinstance(raw_trajectory, dict):
                        raw_steps = raw_trajectory.get("steps", raw_trajectory.get("trajectory", []))
                        if isinstance(raw_steps, list):
                            trajectory = [step for step in raw_steps if isinstance(step, dict)]
                        raw_warning = raw_trajectory.get("warning")
                        if isinstance(raw_warning, str) and raw_warning.strip():
                            trajectory_warning = raw_warning.strip()
                except Exception:  # noqa: BLE001
                    pass
            parsed_messages.append(
                ChatMessageDTO(
                    role=m.role,
                    content=m.content,
                    created_at=m.created_at,
                    sources=srcs,
                    trajectory=trajectory,
                    trajectory_warning=trajectory_warning,
                )
            )

        return ChatDetailResponse(
            chat_id=summary.chat_id,
            title=summary.title,
            messages=parsed_messages,
        )

    # --- DELETE /v1/chats/{chat_id} + DB?---
    @app.delete("/v1/chats/{chat_id}", response_model=ChatDeleteResponse)
    async def delete_chat(chat_id: str) -> ChatDeleteResponse:
        try:
            deleted = await asyncio.to_thread(
                clear_session, chat_id, state.settings.db_path
            )
        except Exception as e:  # noqa: BLE001 — clear_session 不吞 DB 异常，这里兜底返 500
            raise _api_error("INTERNAL", f"删除会话失败: {e}", 500) from e
        if not deleted:
            raise _api_error("NOT_FOUND", f"会话不存在:  {chat_id}", 404)
        return ChatDeleteResponse(chat_id=chat_id, deleted=True)

    # --- GET /v1/documents ---
    @app.get("/v1/documents", response_model=ListDocumentsResponse)
    async def list_documents(
        collection: str | None = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
        format: str | None = Query(None),
        q: str | None = Query(None, min_length=1, description="Search query."),
        sort: str = Query("created_at_desc"),
    ) -> ListDocumentsResponse:
        store = await asyncio.to_thread(state.ensure_open)
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
                    source_missing=(
                        not d.source.startswith("note:")
                        and not Path(d.source).is_file()
                    ),
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
        store = await asyncio.to_thread(state.ensure_open)
        match = store.get_document_by_id(doc_id)
        if match is None:
            raise _api_error("NOT_FOUND", f"文档不存在:  {doc_id}", 404)

        # 真实 chunks 预览：按 chunk_index 顺序取前 N 条，内容截断
        preview = []
        if chunks > 0:
            stored = store.list_chunks_by_document(doc_id, limit=chunks)
            for c in stored:
                content = c.content
                if chunk_content_length and len(content) > chunk_content_length:
                    content = content[:chunk_content_length] + "..."
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
        store = await asyncio.to_thread(state.ensure_open)

        def _do_delete() -> int:
            # 写互斥：与 ingest / reindex 串行
            with state._write_lock:
                return int(store.delete_document(doc_id))

        n = await asyncio.to_thread(_do_delete)
        if n == -1:
            raise _api_error("NOT_FOUND", f"文档不存在:  {doc_id}", 404)
        if n == -2:
            # Qu?
            # 且 30 天内可通过 /v1/trash/{id}/restore 恢复
            return DeleteResponse(id=doc_id, deleted_chunks=0, status="already_deleted")
        return DeleteResponse(id=doc_id, deleted_chunks=n)

    # --- POST /v1/trash/{doc_id}/restore（恢复软删除文档，仅元数据） ---
    @app.post("/v1/trash/{doc_id}/restore", response_model=RestoreResponse)
    async def restore_document(doc_id: str) -> RestoreResponse:
        """Documentation."""
        store = await asyncio.to_thread(state.ensure_open)

        def _do_restore() -> bool:
            with state._write_lock:
                return bool(store.restore_document(doc_id))

        ok = await asyncio.to_thread(_do_restore)
        return RestoreResponse(
            id=doc_id,
            status="restored" if ok else "not_found",
            note=(
                "Index content changed; rerun reindex to regenerate vectors."
                if ok
                else "No reindex needed."
            ),
        )

    # --- GET /v1/trash（列出回收站中的软删除文档） ---
    @app.get("/v1/trash", response_model=TrashListResponse)
    async def list_trash(limit: int = Query(100, ge=1, le=500)) -> TrashListResponse:
        store = await asyncio.to_thread(state.ensure_open)
        try:
            items = await asyncio.to_thread(store.list_trash, limit)
        except StoreError as e:
            raise _api_error("INTERNAL", f"读取回收站失败: {e}", 500) from e
        return TrashListResponse(items=[TrashItem(**it) for it in items], total=len(items))

    # --- POST /v1/trash/purge（物理?N 天前的站?---
    @app.post("/v1/trash/purge", response_model=PurgeTrashResponse)
    async def purge_trash(body: PurgeTrashRequest) -> PurgeTrashResponse:
        """Documentation."""
        store = await asyncio.to_thread(state.ensure_open)

        def _do_purge() -> int:
            with state._write_lock:
                return int(store.purge_trash(body.older_than_days))

        purged = await asyncio.to_thread(_do_purge)
        return PurgeTrashResponse(purged=purged)

    # --- GET /v1/curate-runs（列出近 N 天的 curate 运行记录） ---
    @app.get("/v1/curate-runs", response_model=CurateRunsResponse)
    async def list_curate_runs(
        days: int = Query(7, ge=1, le=90),
        limit: int = Query(50, ge=1, le=500),
    ) -> CurateRunsResponse:
        """Documentation."""
        store = await asyncio.to_thread(state.ensure_open)
        try:
            items = await asyncio.to_thread(store.list_curate_runs, days, limit)
        except StoreError as e:
            raise _api_error("INTERNAL", f"读取 curate 运行记录失败: {e}", 500) from e
        return CurateRunsResponse(
            items=[CurateRunItem(**it) for it in items],
            total=len(items),
        )

    # --- PUT /v1/chunks/{chunk_id}/annotation?---
    @app.put("/v1/chunks/{chunk_id}/annotation")
    async def upsert_chunk_annotation(chunk_id: int, body: dict = Body(...)) -> dict:
        """Store a user annotation in the chunk's extra metadata."""
        text = body.get("text")
        if text is None:
            text = body.get("annotation", "")
        if not isinstance(text, str):
            raise _api_error("BAD_REQUEST", "text must be a string.", 400)
        store = await asyncio.to_thread(state.ensure_open)
        updated = await asyncio.to_thread(
            store.update_chunk_extra,
            chunk_id,
            {"annotation": text},
        )
        if not updated:
            raise _api_error("NOT_FOUND", f"Chunk not found: {chunk_id}", 404)
        return {"chunk_id": chunk_id, "annotation": text, "updated": True}

    @app.get("/v1/config/retrieval-recommended")
    async def retrieval_recommended_preview_endpoint() -> dict[str, Any]:
        """Preview recommended retrieval settings without changing configuration."""
        from doc2mind.core.config import recommended_retrieval_preview

        return await asyncio.to_thread(recommended_retrieval_preview, get_settings())

    @app.post("/v1/config/retrieval-recommended")
    async def retrieval_recommended_apply(
        req: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Documentation."""
        from doc2mind.core.config import (
            apply_recommended_retrieval,
            recommended_retrieval_preview,
            save_settings,
            set_settings,
        )

        body = req or {}
        fields = body.get("fields")
        if fields is not None and not isinstance(fields, list):
            raise _api_error("BAD_REQUEST", "fields R", 400)
        current = get_settings()
        preview = recommended_retrieval_preview(current)
        updated, changes = apply_recommended_retrieval(
            current,
            fields=[str(f) for f in fields] if fields is not None else None,
        )
        if changes:
            ok = await asyncio.to_thread(save_settings, updated)
            if not ok:
                raise _api_error(
                    "INTERNAL", "Failed to apply configuration.", 500
                )
            set_settings(updated)
            try:
                state.settings = updated
            except Exception:  # noqa: BLE001
                pass
        return {
            "aligned_before": preview["aligned"],
            "applied": bool(changes),
            "changes": changes,
            "description": preview["description"],
            "current": {
                name: getattr(updated, name)
                for name in preview["recommended"]
            },
        }

    # --- POST /v1/eval/library：本库索自评（F1?---
    @app.post("/v1/eval/library")
    async def eval_library(
        req: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Documentation."""
        body = req or {}
        collection = body.get("collection")
        sample = int(body.get("sample") or 30)
        seed = int(body.get("seed") or 42)
        top_k = int(body.get("top_k") or 5)
        no_embed = bool(body.get("no_embed"))

        s = get_settings()
        store = await asyncio.to_thread(state.ensure_open)
        embedder = None
        if not no_embed:
            try:
                from doc2mind.core.embedder.factory import get_embedder

                embedder = await asyncio.to_thread(get_embedder, s)
            except Exception as e:  # noqa: BLE001
                # 降级?BM25，报告里标?degraded
                logger.warning("eval_library ?BM25: %s", e)

        from doc2mind.core.eval_library import evaluate_library

        report = await asyncio.to_thread(
            evaluate_library,
            store,
            embedder,
            collection,
            sample,
            seed,
            top_k,
            s,
        )
        return report

    # --- t图谱?---
    @app.get("/v1/graph/visualize", response_model=GraphResponse)
    async def graph_visualize(
        collection: str | None = Query(None),
        limit: int = Query(200, ge=1, le=1000),
    ) -> GraphResponse:
        """Documentation."""
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
        """Documentation."""
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
        """Documentation."""
        def _fetch() -> list[GraphEntityRelationDTO]:
            graph_store = GraphStore(state.settings.db_path)
            try:
                relations = graph_store.get_entity_relations(entity_id, limit=limit)
                return [GraphEntityRelationDTO.model_validate(r) for r in relations]
            finally:
                graph_store.close()

        return await asyncio.to_thread(_fetch)

    @app.get("/v1/graph/entities/{entity_id}/details", response_model=GraphEntityDetailDTO)
    @app.get(
        "/v1/graph/entity/{entity_id}",
        response_model=GraphEntityDetailDTO,
        deprecated=True,  # AUD-023A?/v1/graph/entities/{id}/details
    )
    async def graph_entity_details(
        entity_id: str,
        limit: int = Query(8, ge=1, le=50),
    ) -> GraphEntityDetailDTO:
        """Documentation."""
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
        """Documentation."""
        llm = _build_llm_client_from_provider_config(
            req.provider_config, state.settings, req.model
        )
        if llm is None:
            raise _api_error(
                "LLM_NOT_CONFIGURED",
                "未配置 LLM。请先配置 API Key。",
                400,
            )

        # 组装蒸馏 Prompt
        snippets_text = "\n\n".join(req.local_snippets) if req.local_snippets else "No local snippets available."
        web_text = "\n\n".join(req.web_references) if req.web_references else "No web references available."
        dialogue_text = req.dialogue_summary or "No dialogue summary available."

        prompt = (
            f"Create a concise Markdown knowledge card for {req.entity_name} ({req.entity_type}).\n"
            f"Use the following local snippets as evidence:\n{snippets_text}\n\n"
            f"Dialogue summary:\n{dialogue_text}\n\n"
            f"Web references:\n{web_text}\n\n"
            "Return only Markdown in this structure:\n"
            f"# {req.entity_name}\n"
            "## Summary\n"
            "## Key facts\n"
            "## Relations\n"
            "## Uncertainties\n"
            "## Suggested tags\n"
            "Use exactly 3-5 tags in the final line.\n"
            "Tags:\n"
            "#Tag1 #Tag2 #Tag3\n"
        )

        try:
            markdown_card = await asyncio.to_thread(
                llm.chat,
                [
                    {"role": "system", "content": "Write a concise evidence-based Markdown knowledge card."},
                    {"role": "user", "content": prompt}
                ]
            )
        except Exception as e:
            logger.error("知识卡片蒸馏失败: %s", e)
            raise _api_error("LLM_ERROR", f"知识卡片蒸馏失败: {e}", 500) from e

        if not markdown_card or not markdown_card.strip():
            markdown_card = f"# {req.entity_name}\n\n> No summary returned."

        # 提取推荐标?
        import re
        tags: list[str] = []
        tag_match = re.search(r"标[?]\s*(#[\w\u4e00-\u9fa5\-_]+(?:\s+#[\w\u4e00-\u9fa5\-_]+)*)", markdown_card)
        if tag_match:
            tags = [t.strip("#").strip() for t in tag_match.group(1).split() if t.strip()]
        if not tags:
            all_tags = re.findall(r"#([\w\u4e00-\u9fa5\-_]+)", markdown_card)
            tags = all_tags[:5] if all_tags else [req.entity_type, req.entity_name]

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
        """Documentation."""
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
        """Documentation."""
        from doc2mind.core.curator import curate
        from doc2mind.core.llm.factory import get_llm_client

        llm = get_llm_client(state.settings)
        if llm is None:
            raise _api_error(
                "BAD_REQUEST",
                "未配置 LLM。请先配置 API Key。",
                400,
            )

        store = await asyncio.to_thread(state.ensure_open)
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
                f"y? {req.output_format}",
                400,
            )
        p = Path(req.input_path)
        if not p.exists():
            raise _api_error("BAD_REQUEST", f"文件不存在: {req.input_path}", 400)
        if not is_supported(p):
            raise _api_error("UNSUPPORTED_FORMAT", f"不支持的文件格式: {p.suffix}", 415)

        # 同步的 loader.extract + convert_document 移到线程池，
        # r?PDF y HttpClient "?
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
                    "INTERNAL", f"写入文件失败: {e}", 500
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
        """Documentation."""
        if not req.content or not req.content.strip():
            raise _api_error("BAD_REQUEST", "集合名不能为空", 400)

        def _do_export() -> CreativeExportResponse:
            res = export_artifact(
                content=req.content,
                target_format=req.format,
                output_path=req.output_path,
                title_override=req.title,
                theme=req.theme,
                theme_colors=req.theme_colors,
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
        """Documentation."""
        if not req.content or not req.content.strip():
            raise _api_error("BAD_REQUEST", "导出内容不能为空", 400)

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

    # --- Artifact version history: drafts survive failed exports and can be restored. ---
    @app.post("/v1/creative/artifacts/{artifact_id}/versions", response_model=ArtifactVersionResponse)
    async def save_artifact_version(artifact_id: str, req: ArtifactVersionRequest) -> ArtifactVersionResponse:
        try:
            record = await asyncio.to_thread(
                save_version,
                _user_data_dir() / "artifacts",
                artifact_id,
                content=req.content,
                artifact_format=req.format,
                title=req.title,
                sources=req.sources,
            )
            return ArtifactVersionResponse(**record)
        except ArtifactHistoryError as exc:
            raise _api_error("ARTIFACT_VERSION_FAILED", str(exc), 400) from exc

    @app.get("/v1/creative/artifacts/{artifact_id}/versions", response_model=list[ArtifactVersionSummary])
    async def get_artifact_versions(artifact_id: str) -> list[ArtifactVersionSummary]:
        try:
            records = await asyncio.to_thread(
                list_versions, _user_data_dir() / "artifacts", artifact_id
            )
            return [ArtifactVersionSummary(**record) for record in records]
        except ArtifactHistoryError as exc:
            raise _api_error("ARTIFACT_VERSION_FAILED", str(exc), 400) from exc

    @app.get("/v1/creative/artifacts/{artifact_id}/versions/{version_id}", response_model=ArtifactVersionResponse)
    async def get_artifact_version(artifact_id: str, version_id: str) -> ArtifactVersionResponse:
        try:
            record = await asyncio.to_thread(
                get_version, _user_data_dir() / "artifacts", artifact_id, version_id
            )
        except ArtifactHistoryError as exc:
            raise _api_error("ARTIFACT_VERSION_FAILED", str(exc), 400) from exc
        if record is None:
            raise _api_error("NOT_FOUND", "Artifact version does not exist", 404)
        return ArtifactVersionResponse(**record)

    # --- POST /v1/reindex ---
    @app.post("/v1/reindex", response_model=JobStatus)
    async def reindex(req: ReindexRequest) -> JobStatus:
        # 真实实现：后台线程重新嵌入 collection 内已有 chunks（任务化）。
        # req.model 非空 → 用目标模型重嵌入（换模型重建索引）：
        #   先创建目标 embedder 探测维度；与现有库维度不一致时不拒绝，
        #   ?_run_reindex drop + ?+ ?
        # store jembedder ?ensure_open 
        # k?
        store = await asyncio.to_thread(state.ensure_open)
        collection = req.collection.strip() if req.collection and req.collection.strip() else None
        job_id = _new_id()

        # 目标嵌入器：按请求模型或当前最新 settings 创建并探测维度
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
                # C?bge-base=768?"U?
                # 写进旧C致数据损坏。用?probe 嵌入制触发加载?
                _ = list(candidate.embed_texts(["probe"]))
            except Exception as e:  # noqa: BLE001
                raise _api_error(
                    "BAD_REQUEST", f"加载嵌入模型失败: {model_name}: {e}", 400
                ) from e
            if candidate.dimension != store.embedding_dim:
                # 维度变化：不拒绝，改为重建向量表（在 _run_reindex 中执行）
                need_rebuild = True
            target_embedder = candidate
        else:
            # 不传 model 表示使用当前配置。不能直接复用 state.embedder：
            # /v1/config ?embed_model/embed_model_pathM?embedder
            # 仍是旧实例。重建任务必须按最新 settings 创建并探测目标模型。
            try:
                target_embedder = get_embedder(state.settings)
                _ = list(target_embedder.embed_texts(["probe"]))
            except Exception as e:  # noqa: BLE001
                raise _api_error(
                    "BAD_REQUEST", f"加载嵌入模型失败: {e}", 400
                ) from e
            need_rebuild = target_embedder.dimension != store.embedding_dim

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
            state.job_cancel_events[job_id] = threading.Event()

        cancel_event = state.job_cancel_events[job_id]

        def _run_reindex() -> None:
            # 写互斥：重建向量表（DROP + 回填）期间禁止 ingest/delete 并发写
            with state._write_lock:
                try:
                    pairs = store.list_chunk_contents(collection)
                    total = len(pairs)
                    with state._jobs_lock:
                        job.total = total
                    if total == 0:
                        # ??
                        # 仍必须使用用户刚配置的 embedder，而不能继续持有旧单例。
                        if cancel_event.is_set():
                            raise _JobCancelledError("Job cancelled")

                    embedder = target_embedder
                    if embedder is None:
                        raise RuntimeError("Embedder is unavailable")
                    # 分批重新嵌入，更新向量，避免载全部结果?
                    # need_rebuild（C化）时不能原?update_embeddings（表结F是旧维度）?
                    # 先收集全部 (chunk_id, embedding)，最后一次性重建向量表并回填。
                    batch_size = 32
                    processed = 0
                    rebuild_pairs: list[tuple[int, object]] = []
                    for i in range(0, total, batch_size):
                        if cancel_event.is_set():
                            raise _JobCancelledError("Job cancelled")
                        batch = pairs[i : i + batch_size]
                        texts = [content for _, content in batch]
                        embeddings = list(embedder.embed_texts(texts))
                        if len(embeddings) != len(batch):
                            raise RuntimeError(
                                f"Embedding batch count mismatch: got {len(embeddings)}, expected {len(batch)}."
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
                            job.progress = round(processed / total, 4) if total > 0 else 0.0
                            snapshot = {
                                "progress": job.progress,
                                "processed": job.processed,
                                "total": job.total,
                                "status": job.status,
                            }
                        _broadcast_job_event(job_id, {"type": "progress", "ts": _now_iso(), **snapshot})

                    if cancel_event.is_set():
                        raise _JobCancelledError("Job cancelled")

                    if need_rebuild:
                        # drop + ?
                        # ?
                        store.rebuild_chunk_embeddings(rebuild_pairs, embedder.dimension)
                    else:
                        # 同维度也清孤儿，避免后续导入 UNIQUE 撞车
                        with contextlib.suppress(Exception):
                            store.purge_orphan_chunks()

                    with state._jobs_lock:
                        if job.status == "cancelled" or cancel_event.is_set():
                            raise _JobCancelledError("Job cancelled")
                        job.status = "completed"
                        job.progress = 1.0
                        job.finished_at = _now_iso()
                    _broadcast_job_event(job_id, {"type": "done", "ts": _now_iso(), "status": "completed", "processed": job.processed})

                    # 换模型重建成功后，把全局 embedder 切换为目标模型，
                    # ?/ u?
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
                                    "Could not persist reindex settings to config.toml."
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.warning("reindex 后同步配置失败：%s", e)
                except _JobCancelledError:
                    with state._jobs_lock:
                        job.status = "cancelled"
                        job.finished_at = _now_iso()
                    _broadcast_job_event(job_id, {"type": "cancelled", "ts": _now_iso()})
                except Exception as e:  # noqa: BLE001
                    with state._jobs_lock:
                        job.status = "failed"
                        job.error = str(e)
                        job.finished_at = _now_iso()
                    _broadcast_job_event(job_id, {"type": "failed", "ts": _now_iso(), "error": str(e)})

        threading.Thread(target=_run_reindex, daemon=True).start()
        return job

    # --- POST /v1/curate（AI t库整理：打标??归??归纳，异步任务） ---
    @app.post("/v1/curate", response_model=JobStatus)
    async def curate_endpoint(req: CurateRequest) -> JobStatus:
        """Documentation."""
        store = await asyncio.to_thread(state.ensure_open)

        # LLM 前置检查：未配置直接 400（否则任务跑一半才发现，浪费一轮轮询）
        try:
            llm = get_llm_client(state.settings)
        except LLMError as e:
            raise _api_error("BAD_REQUEST", f"LLM 配置不可用: {e}", 400) from e
        if llm is None:
            raise _api_error(
                "BAD_REQUEST",
                "LLM is not configured. "
                "Set DOC2MIND_LLM_PROVIDER to enable curation.",
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
            state.job_cancel_events[job_id] = threading.Event()

        cancel_event = state.job_cancel_events[job_id]

        def _update_curate_job(done: int, total: int) -> None:
            if cancel_event.is_set():
                raise _JobCancelledError("Job cancelled")
            with state._jobs_lock:
                job.processed = done
                job.total = total
                job.progress = round(done / total, 4) if total > 0 else 0.0
                snapshot = {
                    "progress": job.progress,
                    "processed": job.processed,
                    "total": job.total,
                    "status": job.status,
                }
            _broadcast_job_event(job_id, {"type": "progress", "ts": _now_iso(), **snapshot})

        def _run_curate() -> None:
            from doc2mind.core.curator import CurateCancelledError
            from doc2mind.core.curator import curate as run_curate

            try:
                # 写互斥?ingest / delete / reindex 串（dry_run 虽只读，
                # 也统且，避免与并发?dedup 执今互相p?
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
                        cancel_check=cancel_event.is_set,
                        note=req.note,
                    )
                if cancel_event.is_set():
                    raise _JobCancelledError("Job cancelled")
                with state._jobs_lock:
                    if job.status == "cancelled":
                        raise _JobCancelledError("Job cancelled")
                    job.status = "completed"
                    job.progress = 1.0
                    job.finished_at = _now_iso()
                    job.report = report.to_dict()
                _broadcast_job_event(job_id, {"type": "done", "ts": _now_iso(), "status": "completed", "processed": job.processed})
            except (_JobCancelledError, CurateCancelledError):
                with state._jobs_lock:
                    job.status = "cancelled"
                    job.finished_at = _now_iso()
                _broadcast_job_event(job_id, {"type": "cancelled", "ts": _now_iso()})
            except Exception as e:  # noqa: BLE001
                with state._jobs_lock:
                    job.status = "failed"
                    job.error = str(e)
                    job.finished_at = _now_iso()
                _broadcast_job_event(job_id, {"type": "failed", "ts": _now_iso(), "error": str(e)})

        threading.Thread(target=_run_curate, daemon=True).start()
        return job

    @app.post("/v1/agent/permission/{request_id}")
    async def resolve_agent_permission(request_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Resolve a pending Agent workspace-write permission request."""
        decision = str(body.get("decision", "deny")).strip().lower()
        if decision not in {"allow", "deny"}:
            raise _api_error("BAD_REQUEST", "decision must be allow or deny.", 400)
        from doc2mind.core.agent.runtime.permissions import GLOBAL_PERMISSION_BROKER

        resolved = GLOBAL_PERMISSION_BROKER.resolve(request_id, decision)
        return {"resolved": resolved, "request_id": request_id, "decision": decision}

    # --- POST /v1/agent/jobs ---
    @app.post("/v1/agent/jobs", response_model=JobStatus)
    async def start_agent_job(req: ChatRequest) -> JobStatus:
        """Start a durable Agent run whose terminal response can be reattached."""
        if not bool(getattr(state.settings, "agent_mode_enabled", False)):
            raise _api_error(
                "AGENT_DISABLED",
                "Agent background jobs require agent_mode_enabled=true.",
                409,
            )

        store = await asyncio.to_thread(state.ensure_open)
        try:
            llm_client = _build_llm_client_from_provider(req, state.settings)
        except LLMError as e:
            raise _api_error("BAD_REQUEST", f"LLM configuration error: {e}", 400) from e

        job_id = _new_id()
        chat_id = req.chat_id or f"agent-{_new_id()}"
        req = req.model_copy(
            update={"chat_id": chat_id, "agent_mode": True, "mode": "agent", "chat_mode": "agent"}
        )
        job = JobStatus(
            job_id=job_id,
            type="agent",
            status="running",
            progress=0.0,
            processed=0,
            total=1,
            stage="queued",
            stage_progress=0.0,
            started_at=_now_iso(),
        )
        with state._jobs_lock:
            state._prune_jobs_locked()
            state.jobs[job_id] = job
            state.job_cancel_events[job_id] = threading.Event()
        cancel_event = state.job_cancel_events[job_id]

        def update_job(*, stage: str, progress: float, current_file: str | None = None) -> None:
            with state._jobs_lock:
                job.stage = stage
                job.stage_progress = max(0.0, min(progress, 1.0))
                job.progress = job.stage_progress
                job.processed = 1 if progress >= 1.0 else 0
                if current_file is not None:
                    job.current_file = current_file
                snapshot = {
                    "status": job.status,
                    "progress": job.progress,
                    "processed": job.processed,
                    "total": job.total,
                    "stage": job.stage,
                    "stage_progress": job.stage_progress,
                    "current_file": job.current_file,
                    "permission_request_id": job.permission_request_id,
                    "permission_tool_id": job.permission_tool_id,
                    "permission_timeout_sec": job.permission_timeout_sec,
                }
            _broadcast_job_event(job_id, {"type": "progress", "ts": _now_iso(), **snapshot})

        def run_agent_job() -> None:
            from doc2mind.core.agent.runtime.chat_agent import agent_answer_stream

            answer_parts: list[str] = []
            artifacts: list[dict[str, Any]] = []
            done: dict[str, Any] | None = None
            stage_progress = {
                "planning": 0.12,
                "running_tool": 0.40,
                "waiting_permission": 0.45,
                "preparing_artifact": 0.84,
                "generating_answer": 0.90,
            }
            try:
                update_job(stage="planning", progress=stage_progress["planning"])
                stream = agent_answer_stream(
                    req.query,
                    collection=req.collection,
                    top_k=req.top_k,
                    chat_id=req.chat_id,
                    settings=state.settings,
                    llm_client=llm_client,
                    collections=req.collections,
                    model_override=req.model,
                    enable_web_search=req.enable_web_search,
                    web_search_mode=req.resolved_web_search_mode(),
                    github_token=req.github_token,
                    store=store,
                    embedder=state.embedder,
                    stop_event=cancel_event,
                    persona=req.persona,
                    persona_prompt=req.persona_prompt,
                    attachments=req.attachments,
                    memory_context=req.memory_context,
                    task_context=req.task_context,
                    response_mode=req.response_mode,
                    answer_format=req.answer_format,
                )
                for frame in stream:
                    if cancel_event.is_set():
                        # The generator observes this event and performs its own orderly shutdown.
                        continue
                    try:
                        payload = json.loads(frame)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if token := payload.get("token"):
                        answer_parts.append(str(token))
                        update_job(stage="generating_answer", progress=stage_progress["generating_answer"])
                        continue

                    event_type = str(payload.get("type") or "")
                    if event_type == "agent_plan":
                        update_job(stage="planning", progress=0.22)
                    elif event_type == "tool_call":
                        update_job(
                            stage="running_tool",
                            progress=stage_progress["running_tool"],
                            current_file=str(payload.get("tool_id") or ""),
                        )
                    elif event_type == "tool_result":
                        update_job(stage="running_tool", progress=0.60)
                    elif event_type == "permission_request":
                        with state._jobs_lock:
                            job.permission_request_id = str(payload.get("request_id") or "") or None
                            job.permission_tool_id = str(payload.get("tool_id") or "") or None
                            job.permission_timeout_sec = int(payload.get("timeout_sec") or 60)
                        update_job(
                            stage="waiting_permission",
                            progress=stage_progress["waiting_permission"],
                            current_file=str(payload.get("tool_id") or ""),
                        )
                    elif event_type == "permission_resolved":
                        with state._jobs_lock:
                            job.permission_request_id = None
                            job.permission_tool_id = None
                            job.permission_timeout_sec = None
                        update_job(stage="running_tool", progress=0.52)
                    elif event_type == "artifact_ready":
                        artifacts.append(dict(payload))
                        update_job(stage="preparing_artifact", progress=stage_progress["preparing_artifact"])
                    elif event_type == "status":
                        message = str(payload.get("message") or "")
                        if "生成" in message:
                            update_job(stage="generating_answer", progress=stage_progress["generating_answer"])
                    elif payload.get("done"):
                        done = payload

                if cancel_event.is_set():
                    raise _JobCancelledError("Job cancelled")
                if done is None:
                    raise RuntimeError("Agent stream ended without a completion frame.")

                final_artifacts = done.get("artifacts")
                if isinstance(final_artifacts, list):
                    artifacts = [item for item in final_artifacts if isinstance(item, dict)]
                with state._jobs_lock:
                    job.result = AgentJobResultDTO(
                        answer="".join(answer_parts),
                        chat_id=str(done.get("chat_id") or chat_id),
                        model=str(done.get("model") or ""),
                        provider=str(done.get("provider") or ""),
                        total_chunks=int(done.get("total_chunks") or 0),
                        elapsed_ms=int(done.get("elapsed_ms") or 0),
                        sources=done.get("sources") or [],
                        trajectory=done.get("trajectory") or [],
                        trajectory_warning=done.get("trajectory_warning"),
                        partial=bool(done.get("partial")),
                        warning=done.get("warning"),
                        artifacts=artifacts,
                    )
                    job.status = "completed"
                    job.progress = 1.0
                    job.processed = 1
                    job.stage = "completed"
                    job.stage_progress = 1.0
                    job.permission_request_id = None
                    job.permission_tool_id = None
                    job.permission_timeout_sec = None
                    job.finished_at = _now_iso()
                _broadcast_job_event(job_id, {"type": "done", "ts": _now_iso(), "status": "completed"})
            except _JobCancelledError:
                with state._jobs_lock:
                    job.status = "cancelled"
                    job.stage = "cancelled"
                    job.permission_request_id = None
                    job.permission_tool_id = None
                    job.permission_timeout_sec = None
                    job.finished_at = _now_iso()
                _broadcast_job_event(job_id, {"type": "cancelled", "ts": _now_iso()})
            except Exception as e:  # noqa: BLE001
                logger.exception("Agent background job failed: %s", job_id)
                with state._jobs_lock:
                    job.status = "failed"
                    job.error = str(e)
                    job.stage = "failed"
                    job.permission_request_id = None
                    job.permission_tool_id = None
                    job.permission_timeout_sec = None
                    job.finished_at = _now_iso()
                _broadcast_job_event(job_id, {"type": "failed", "ts": _now_iso(), "error": str(e)})

        threading.Thread(target=run_agent_job, name=f"agent-job-{job_id[:8]}", daemon=True).start()
        return job

    # --- GET /v1/jobs/{id} ---
    @app.get("/v1/jobs/{job_id}", response_model=JobStatus)
    async def get_job(job_id: str) -> JobStatus:
        with state._jobs_lock:
            state._prune_jobs_locked()
            job = state.jobs.get(job_id)
        if job is None:
            raise _api_error("NOT_FOUND", f"任务不存在: {job_id}", 404)
        return job

    # --- DELETE /v1/jobs/{id}（取消任务?---
    @app.delete("/v1/jobs/{job_id}", response_model=JobStatus)
    async def cancel_job(job_id: str) -> JobStatus:
        """Documentation."""
        with state._jobs_lock:
            job = state.jobs.get(job_id)
            if job is None:
                raise _api_error("NOT_FOUND", f"任务不存在: {job_id}", 404)
            if job.status in ("pending", "running"):
                state.job_cancel_events.setdefault(job_id, threading.Event()).set()
                job.status = "cancelled"
                job.finished_at = _now_iso()
        return job

    # --- GET /v1/events (SSE) ---
    @app.get("/v1/events")
    async def events() -> Any:
        """Documentation."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str] = asyncio.Queue()
        entry = (loop, q)
        with _sse_lock:
            _SSE_CONNECTIONS.add(entry)

        async def event_stream() -> Any:
            try:
                # 握手就绪?
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

    # --- ?关闭钩子 ---
    @app.on_event("startup")
    async def _startup() -> None:
        # ?A?ONNX 
        # （约 1-3 秒，与 factory 缓存共享同一实例）。模型未下载或加载
        # ?
        def _preheat() -> None:
            try:
                state.ensure_open()
                embedder = state.embedder
                if embedder is not None:
                    _ = list(embedder.embed_texts(["warmup"]))
                    logger.info("embedder preheat complete: %s", getattr(embedder, "model_name", "?"))
                if state.settings.rerank_enabled:
                    reranker = get_reranker(state.settings)
                    if reranker is not None:
                        reranker.rerank("warmup", ["warmup"])
                        logger.info("reranker preheat complete: %s", reranker.model_name)
                    logger.info("嵌入模型预热完成: %s", getattr(embedder, "model_name", "?"))
            except Exception as e:  # noqa: BLE001
                logger.warning("嵌入模型后台预热失败（首次使用时再加载）: %s", e)

        # DOC2MIND_EMBED_PREHEAT=0 跳过后台预热：预热会触发嵌入模型懒加载
        # 下载，fastembed 下载内部的线程池会让解释器退出挂起（离线机器/测试
        # 环境实测）；测试 conftest 全局置 0。
        if os.environ.get("DOC2MIND_EMBED_PREHEAT", "1").strip().lower() not in ("0", "false", "off"):
            threading.Thread(target=_preheat, daemon=True, name="embedder-preheat").start()

        if state.settings.watch_paths:
            try:
                from doc2mind.core.file_watcher import FileWatcher

                state.file_watcher = FileWatcher(
                    paths=state.settings.watch_paths,
                    settings=state.settings,
                    debounce_seconds=state.settings.watch_debounce_seconds,
                    # 共享单例 store 与全局写锁：修复 watcher 旁路写锁、
                    # 每文件重建 store/embedder 的历史问题
                    store_provider=lambda: state.store,
                    write_lock=state._write_lock,
                    on_ingested=lambda payload: _broadcast_event({"type": "file_ingested", "ts": _now_iso(), **payload}),
                )
                state.file_watcher.start()
            except Exception as e:  # noqa: BLE001
                logger.warning("? %s", e)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if state.file_watcher is not None:
            try:
                state.file_watcher.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("? %s", e)
        if state.store is not None:
            state.store.close()

    return app


# --- 辅?---
def _api_error(code: str, message: str, status: int) -> Any:
    """Documentation."""
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
    stage: str | None = None,
    stage_progress: float | None = None,
    stage_detail: str | None = None,
) -> None:
    """Documentation."""
    frac = stage_fraction(stage, stage_progress)
    with state._jobs_lock:
        job.processed = done
        job.total = total
        job.stage = stage
        job.stage_progress = stage_progress
        job.stage_detail = stage_detail
        new_progress = round((done + frac) / total, 4) if total > 0 else 0.0
        # ingest_workers>1?
        # r?
        job.progress = max(job.progress, new_progress)
        if current_file is not None:
            job.current_file = current_file
        snapshot = {
            "progress": job.progress,
            "processed": job.processed,
            "total": job.total,
            "current_file": job.current_file,
            "stage": job.stage,
            "stage_progress": job.stage_progress,
            "stage_detail": job.stage_detail,
            "status": job.status,
        }
    _broadcast_job_event(job.job_id, {"type": "progress", "ts": _now_iso(), **snapshot})


def _update_curate_job(
    state: _AppState,
    job: JobStatus,
    done: int,
    total: int,
) -> None:
    """Documentation."""
    with state._jobs_lock:
        job.processed = done
        job.total = total
        job.progress = round(done / total, 4) if total > 0 else 0.0
        snapshot = {
            "progress": job.progress,
            "processed": job.processed,
            "total": job.total,
            "status": job.status,
        }
    _broadcast_job_event(job.job_id, {"type": "progress", "ts": _now_iso(), **snapshot})


def _spawn_background_curate(
    state: _AppState,
    document_ids: list[str],
    collection: str,
) -> str | None:
    """Documentation."""
    if not document_ids:
        return None
    job_id = _new_id()
    job = JobStatus(
        job_id=job_id,
        type="curate",
        status="running",
        progress=0.0,
        processed=0,
        total=len(document_ids),
        started_at=_now_iso(),
    )
    with state._jobs_lock:
        state.jobs[job_id] = job
        state.job_cancel_events[job_id] = threading.Event()
    cancel_event = state.job_cancel_events[job_id]

    def _run() -> None:
        try:
            store = state.ensure_open()
            result = run_background_curate(
                state.settings,
                document_ids,
                collection,
                store=store,
                write_lock=state._write_lock,
                cancel_event=cancel_event,
                progress=lambda done, total: _update_curate_job(state, job, done, total),
            )
            with state._jobs_lock:
                if result.get("cancelled"):
                    job.status = "cancelled"
                else:
                    job.status = "completed"
                    job.progress = 1.0
                job.processed = result["processed"] + result["failed"]
                job.finished_at = _now_iso()
            if job.status == "cancelled":
                _broadcast_job_event(job_id, {"type": "cancelled", "ts": _now_iso()})
            else:
                _broadcast_job_event(
                    job_id,
                    {"type": "done", "ts": _now_iso(), "status": "completed",
                     "processed": job.processed},
                )
        except Exception as e:  # noqa: BLE001
            with state._jobs_lock:
                if job.status == "cancelled":
                    return
                job.status = "failed"
                job.error = str(e)
                job.finished_at = _now_iso()
            _broadcast_job_event(job_id, {"type": "failed", "ts": _now_iso(), "error": str(e)})

    threading.Thread(target=_run, daemon=True, name=f"curate-{job_id[:8]}").start()
    return job_id
