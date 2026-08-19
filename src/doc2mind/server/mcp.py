"""MCP Server — 暴露 12 个工具给 Cursor / Claude Desktop / Windsurf 等 AI 工具。

传输方式：stdio（MCP 默认）

暴露的工具：
    ingest         path, collection="default", recursive=False, force=False
    search         query, collection="default", top_k=10
    ingest_text    text, title="", collection=None（AI 自动归类）, force=False
    ingest_job     path, collection="default", recursive=False, force=False
    get_job        job_id
    list_docs      collection=None, limit=50
    remove_doc     target (file path or doc_id), collection="default"
    quality_check  collection="default"
    convert_file   input_path, output_format="md"
    reindex        collection="default", model=None
    chat           query, collection="default", top_k=5, chat_id=None
    curate         collection=None, actions=None, dry_run=True, top_k=10

启动：
    doc2mind mcp

接入：在 AI 工具的 MCP 配置里注册：
    { "mcpServers": { "doc2mind": { "command": "doc2mind", "args": ["mcp"] } } }
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import uuid
from datetime import datetime, timezone
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
from doc2mind.core.rag import RagError, rag_answer
from doc2mind.core.retriever.search import Retriever
from doc2mind.core.store.sqlite_vec import VectorStore

# --- 模块级异步任务存储（MCP 进程内，供 ingest_job / reindex / get_job 用） ---
_JOB_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _create_job(job_type: str) -> tuple[str, dict[str, Any]]:
    """创建并登记一个异步任务，返回 (job_id, job)。"""
    job_id = f"{job_type}-{uuid.uuid4().hex[:12]}"
    job: dict[str, Any] = {
        "job_id": job_id,
        "type": job_type,
        "status": "running",
        "progress": 0.0,
        "processed": 0,
        "total": 0,
        "started_at": _now_iso(),
        "finished_at": None,
        "error": None,
    }
    with _JOB_LOCK:
        _JOBS[job_id] = job
    return job_id, job


def _update_job(job_id: str, **kwargs: Any) -> None:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(kwargs)


def _get_job(job_id: str) -> dict[str, Any] | None:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None


# --- 工具实现 ---
def _open_store() -> tuple[VectorStore, Any]:
    settings = get_settings()
    embedder = get_embedder(settings)
    store = VectorStore(settings.db_path, embedder.dimension)
    store.open()
    return store, embedder


def _tool_ingest(
    path: str,
    collection: str = "default",
    recursive: bool = False,
    force: bool = False,
) -> str:
    """摄入文档或目录。"""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return _error("BAD_REQUEST", f"路径不存在: {path}")
    summary = ingest_path(
        path=p,
        collection=collection,
        recursive=recursive,
        force=force,
    )
    return _ok({
        "ingested": summary.total_documents,
        "skipped": summary.skipped,
        "failed": summary.failed,
        "total_chunks": summary.total_chunks,
        "results": [
            {
                "source": r.source,
                "format": r.format,
                "chunks": r.chunk_count,
                "status": r.status,
                "error": r.error,
            }
            for r in summary.results
        ],
    })


def _tool_ingest_job(
    path: str,
    collection: str = "default",
    recursive: bool = False,
    force: bool = False,
) -> str:
    """异步摄入文档或目录：立即返回 job_id，后台线程逐文件处理，
    用 get_job 轮询进度。适合大目录/中大型项目（避免同步调用超时）。"""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return _error("BAD_REQUEST", f"路径不存在: {path}")

    job_id, job = _create_job("ingest")
    # 任务需要独立打开的 store（后台线程不共享主循环的 store 连接）
    settings = get_settings()
    embedder = get_embedder(settings)
    store = VectorStore(settings.db_path, embedder.dimension)
    store.open()

    def _run() -> None:
        try:
            summary = ingest_path(
                path=p,
                collection=collection,
                recursive=recursive,
                force=force,
                store=store,
                progress=lambda done, total: _update_job(
                    job_id, processed=done, total=total,
                    progress=round(done / total, 4) if total > 0 else 0.0,
                ),
            )
            _update_job(
                job_id, status="completed", progress=1.0,
                processed=summary.total_documents + summary.skipped + summary.failed,
                finished_at=_now_iso(),
            )
        except Exception as e:  # noqa: BLE001
            _update_job(job_id, status="failed", error=str(e), finished_at=_now_iso())
        finally:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_run, daemon=True).start()
    return _ok({
        "job_id": job_id,
        "status": job["status"],
        "message": "异步摄入已提交，用 get_job 查询进度。",
    })


def _tool_get_job(job_id: str) -> str:
    """查询异步任务状态（ingest_job / reindex 提交的任务）。"""
    job = _get_job(job_id)
    if job is None:
        return _error("NOT_FOUND", f"任务不存在: {job_id}")
    return _ok(job)


def _tool_ingest_text(
    text: str,
    title: str = "",
    collection: str | None = None,
    force: bool = False,
) -> str:
    """直接摄入一段文本到知识库（AI 沉淀经验/笔记/结论用）。

    与 ingest 不同：不依赖文件路径，把 text 内容直接分块、嵌入、入库。
    同内容（MD5）默认跳过；force=True 强制重新摄入。
    collection 不传时：入库后由 AI 自动打标签、生成摘要并归类到
    合适的集合（需要配置 LLM，auto_curate_on_ingest 开启时生效）。
    """
    if not text or not text.strip():
        return _error("BAD_REQUEST", "text 不能为空")
    result = ingest_text(
        text=text,
        title=title,
        collection=collection,
        force=force,
    )
    return _ok({
        "source": result.source,
        "collection": result.collection,
        "status": result.status,
        "chunks": result.chunk_count,
        "document_id": result.document_id,
        "error": result.error,
        # AI 自动整理结果：enrich（title/tags/summary）+ categorize（最终集合）
        "curation": result.curation,
    })


def _tool_search(
    query: str,
    collection: str = "default",
    top_k: int = 10,
) -> str:
    """混合检索 Top-K。"""
    store, embedder = _open_store()
    try:
        retriever = Retriever(store=store, embedder=embedder)
        hits, stats = retriever.search(
            query=query, collection=collection, top_k=top_k
        )
    finally:
        store.close()

    return _ok({
        "query": query,
        "total": len(hits),
        "elapsed_ms": stats.elapsed_ms,
        "hits": [
            {
                "rank": h.rank,
                "score": round(h.score, 4),
                "match_type": h.match_type,
                "source": h.chunk.source,
                "format": h.chunk.format,
                "page": h.chunk.page,
                "heading": h.chunk.heading,
                "content": h.chunk.content,
            }
            for h in hits
        ],
    })


def _tool_list_docs(
    collection: str | None = None,
    limit: int = 50,
) -> str:
    """列出已摄入文档。"""
    store, _ = _open_store()
    try:
        docs = store.list_documents(collection=collection, limit=limit)
    finally:
        store.close()

    return _ok({
        "total": len(docs),
        "documents": [
            {
                "id": d.id,
                "source": d.source,
                "collection": d.collection,
                "format": d.format,
                "size_bytes": d.size_bytes,
                "page_count": d.page_count,
                "chunk_count": d.chunk_count,
                "created_at": d.created_at,
            }
            for d in docs
        ],
    })


def _tool_remove_doc(
    target: str,
    collection: str = "default",
) -> str:
    """按文档 ID 或文件路径删除。"""
    from pathlib import Path

    store, _ = _open_store()
    try:
        # 优先按 ID
        if len(target) >= 12 and not Path(target).exists():
            n = store.delete_document(target)
            if n >= 0:
                return _ok({"id": target, "deleted_chunks": n, "status": "deleted"})

        # 按 source 名删
        source_name = Path(target).name if Path(target).exists() else target
        n = store.delete_by_source(source_name, collection)
        if n > 0:
            return _ok({"source": source_name, "deleted": True})
        return _error("NOT_FOUND", f"未找到: {source_name}")
    finally:
        store.close()


def _tool_quality_check(collection: str = "default") -> str:
    """质量检查报告。"""
    store, _ = _open_store()
    try:
        docs = store.list_documents(collection=collection, limit=10000)
        format_dist: dict[str, int] = {}
        total_chunks = 0
        for d in docs:
            format_dist[d.format] = format_dist.get(d.format, 0) + 1
            total_chunks += d.chunk_count

        # 质量告警：与 HTTP /v1/quality 对齐
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
        warnings = warnings[:20]
        if not warnings and docs:
            warnings.append("未发现质量问题")

        return _ok({
            "collection": collection,
            "total_documents": len(docs),
            "total_chunks": total_chunks,
            "format_distribution": format_dist,
            "warnings": warnings,
        })
    finally:
        store.close()


def _tool_convert_file(
    input_path: str,
    output_format: str = "md",
) -> str:
    """格式互转：返回转换后的文本内容。"""
    from pathlib import Path

    if output_format not in SUPPORTED_FORMATS:
        return _error(
            "BAD_REQUEST",
            f"不支持的格式: {output_format}，支持 {list(SUPPORTED_FORMATS)}",
        )
    p = Path(input_path)
    if not p.exists():
        return _error("BAD_REQUEST", f"文件不存在: {input_path}")
    if not is_supported(p):
        return _error("UNSUPPORTED_FORMAT", f"不支持的文件格式: {p.suffix}")

    try:
        loader = get_loader(p)
        doc = loader.extract(p)
        result = convert_document(doc, output_format)
    except ConversionError as e:
        return _error("INTERNAL", str(e))
    except Exception as e:  # noqa: BLE001
        return _error("INTERNAL", f"加载失败: {e}")

    return _ok({
        "input": p.name,
        "output_format": output_format,
        "content": result,
        "elements_count": len(doc.elements),
    })


def _tool_reindex(
    collection: str = "default",
    model: str | None = None,
) -> str:
    """重建指定集合的向量索引：后台线程分批重新嵌入，返回 job_id 用 get_job 轮询进度。

    model 非空时切换嵌入模型；维度变化则重建向量表并回填全部向量。
    """
    job_id, _job = _create_job("reindex")

    settings = get_settings()
    embedder = get_embedder(settings)
    store = VectorStore(settings.db_path, embedder.dimension)
    store.open()

    # 目标嵌入器：默认复用当前；指定 model 时探测维度，维度变化需重建向量表
    target_embedder: Any = embedder
    need_rebuild = False
    if model and model.strip():
        from dataclasses import replace

        model_name = model.strip()
        try:
            target_settings = replace(settings, embed_model=model_name)
            candidate = get_embedder(target_settings)
            # 真实加载目标模型并探测维度：dimension 属性在加载前只返回预设值
            # （默认 512），直接判断 need_rebuild 会在换维度模型时误判，把新维度
            # 向量写进旧维度表导致损坏。用一次 probe 嵌入强制触发加载。
            _ = list(candidate.embed_texts(["probe"]))
        except Exception as e:  # noqa: BLE001
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass
            return _error("BAD_REQUEST", f"加载嵌入模型失败: {model_name}: {e}")
        if candidate.dimension != store.embedding_dim:
            need_rebuild = True
        target_embedder = candidate

    def _run() -> None:
        try:
            pairs = store.list_chunk_contents(collection)
            total = len(pairs)
            _update_job(job_id, total=total)
            if total == 0:
                _update_job(job_id, status="completed", progress=1.0, finished_at=_now_iso())
                return

            BATCH = 32
            processed = 0
            rebuild_pairs: list[tuple[int, object]] = []
            for i in range(0, total, BATCH):
                batch = pairs[i : i + BATCH]
                texts = [content for _, content in batch]
                embeddings = list(target_embedder.embed_texts(texts))
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
                _update_job(
                    job_id, processed=processed,
                    progress=round(processed / total, 4),
                )

            if need_rebuild:
                store.rebuild_chunk_embeddings(rebuild_pairs, target_embedder.dimension)

            _update_job(job_id, status="completed", progress=1.0, finished_at=_now_iso())
        except Exception as e:  # noqa: BLE001
            _update_job(job_id, status="failed", error=str(e), finished_at=_now_iso())
        finally:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_run, daemon=True).start()
    return _ok({
        "job_id": job_id,
        "status": "running",
        "message": "重建索引任务已提交，用 get_job 查询进度。",
    })


def _tool_chat(
    query: str,
    collection: str = "default",
    top_k: int = 5,
    chat_id: str | None = None,
    collections: list[str] | None = None,
) -> str:
    """RAG 对话问答：检索知识库 → 调用 LLM 生成回答，带来源引用。

    chat_id 传同一值可多轮追问；不传则新建会话。
    collections 支持多选知识库集合，优先于 collection。
    """
    try:
        answer = rag_answer(
            query=query,
            collection=collection,
            top_k=top_k,
            chat_id=chat_id,
            collections=collections,
        )
    except RagError as e:
        return _error("RAG_ERROR", str(e))

    return _ok({
        "answer": answer.answer,
        "chat_id": answer.chat_id,
        "model": answer.model,
        "provider": answer.provider,
        "total_chunks": answer.total_chunks,
        "elapsed_ms": answer.elapsed_ms,
        "sources": [
            {
                "index": s.index,
                "source": s.source,
                "format": s.format,
                "page": s.page,
                "heading": s.heading,
                "score": s.score,
            }
            for s in answer.sources
        ],
    })


def _tool_curate(
    collection: str | None = None,
    actions: list[str] | None = None,
    dry_run: bool = True,
    top_k: int = 10,
) -> str:
    """AI 整理知识库：enrich（打标签/摘要）、categorize（自动归类）、
    dedup（语义去重）、consolidate（归纳合并蒸馏笔记）。

    dry_run=True（默认）只读预览零写入；删除/合并类动作确认预览后
    用 dry_run=False 执行。返回结构化整理报告。
    """
    from doc2mind.core.curator import CuratorError, curate
    from doc2mind.core.llm.base import LLMError
    from doc2mind.core.llm.factory import get_llm_client

    settings = get_settings()
    try:
        llm = get_llm_client(settings)
    except LLMError as e:
        return _error("BAD_REQUEST", f"LLM 配置不可用: {e}")
    if llm is None:
        return _error(
            "BAD_REQUEST",
            "未配置 LLM（llm_provider=none），整理需要大模型；"
            "请设置 DOC2MIND_LLM_PROVIDER（及对应密钥）后重试",
        )

    store, embedder = _open_store()
    try:
        report = curate(
            store=store,
            embedder=embedder,
            llm=llm,
            settings=settings,
            collection=collection,
            actions=actions,
            dry_run=dry_run,
            top_k=top_k,
        )
    except CuratorError as e:
        return _error("CURATE_ERROR", str(e))
    finally:
        store.close()

    return _ok(report.to_dict())


def _tool_graph_get(collection: str = "default", limit: int = 100) -> str:
    from doc2mind.core.store.graph_store import GraphStore

    settings = get_settings()
    store = GraphStore(settings.db_path)
    try:
        data = store.get_graph(collection=collection if collection else None, limit=limit)
        return _ok(data)
    except Exception as e:  # noqa: BLE001
        return _error("INTERNAL", f"查询知识图谱失败: {e}")
    finally:
        store.close()


def _tool_create_artifact(
    content: str,
    format: str = "docx",
    output_path: str | None = None,
    title: str | None = None,
    theme: str | None = None,
) -> str:
    """创作交付物导出：编译生成物理 PPTX / DOCX / XLSX / HTML 文件。"""
    from doc2mind.core.creator import export_artifact

    res = export_artifact(
        content=content,
        target_format=format,
        output_path=output_path,
        title_override=title,
        theme=theme,
    )
    if not res.ok:
        return _error("EXPORT_FAILED", f"导出失败: {res.error}")
    return _ok({
        "ok": True,
        "format": res.artifact_type,
        "file_path": res.file_path,
        "file_name": res.file_name,
        "file_size_bytes": res.file_size_bytes,
    })


def _tool_inspect_artifact(content: str) -> str:
    """创作交付物质量诊断：对 PPT 演示文稿进行全方位效果自检与健康度评分。"""
    from doc2mind.core.creator import inspect_presentation

    report = inspect_presentation(content)
    return _ok({
        "score": report.score,
        "grade": report.grade,
        "summary": report.summary,
        "slide_count": report.slide_count,
        "notes_coverage_pct": report.notes_coverage_pct,
        "archetype_diversity": report.archetype_diversity,
        "total_words": report.total_words,
        "avg_words_per_slide": report.avg_words_per_slide,
        "issues": [
            {
                "level": i.level.value,
                "category": i.category,
                "message": i.message,
                "slide_index": i.slide_index,
                "fix_suggestion": i.fix_suggestion,
            }
            for i in report.issues
        ],
        "recommendations": report.recommendations,
        "highlights": report.highlights,
    })




# --- 工具元数据 ---
TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "ingest",
        "description": "摄入文档或目录到知识库。自动解析、分块、嵌入、入库。已存在的文件（按 MD5 去重）默认跳过。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要摄入的文件或目录的绝对路径。"},
                "collection": {"type": "string", "default": "default", "description": "集合名称。"},
                "recursive": {"type": "boolean", "default": False, "description": "目录是否递归扫描。"},
                "force": {"type": "boolean", "default": False, "description": "强制重新摄入未变更文件。"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search",
        "description": "在知识库中执行混合检索（BM25 + 向量余弦，RRF 融合），返回 Top-K 命中分块。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询词。"},
                "collection": {"type": "string", "default": "default"},
                "top_k": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ingest_text",
        "description": "直接摄入一段文本到知识库（AI 沉淀经验/笔记/结论用，不依赖文件路径）。自动分块、嵌入、入库；同内容（MD5）默认跳过，force=True 强制重新摄入。collection 不传时由 AI 自动打标签、生成摘要并归类到合适的集合（需配置 LLM）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要入库的文本内容（经验、结论、要点等）。"},
                "title": {"type": "string", "description": "标题（可选），留空自动生成。"},
                "collection": {"type": "string", "description": "集合名称（可选）。不传时由 AI 自动归类；传值则尊重显式选择。"},
                "force": {"type": "boolean", "default": False, "description": "相同内容强制重新摄入。"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "ingest_job",
        "description": "异步摄入文档或目录：立即返回 job_id，后台线程逐文件处理，用 get_job 轮询进度。适合大目录/中大型项目（避免同步调用超时）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要摄入的文件或目录的绝对路径。"},
                "collection": {"type": "string", "default": "default", "description": "集合名称。"},
                "recursive": {"type": "boolean", "default": False, "description": "目录是否递归扫描。"},
                "force": {"type": "boolean", "default": False, "description": "强制重新摄入未变更文件。"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_job",
        "description": "查询异步任务状态（ingest_job / reindex 提交的任务）：progress 0-1、processed/total、status（running/completed/failed）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "任务 ID（由 ingest_job / reindex 返回）。"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "list_docs",
        "description": "列出已摄入的文档及其元数据（分块数、大小、格式等）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "集合名，省略则跨所有集合。"},
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 1000},
            },
        },
    },
    {
        "name": "remove_doc",
        "description": "从知识库删除单个文档及其所有分块与向量。target 可以是文档 ID（ULID/UUID）或文件路径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "文档 ID 或文件路径。"},
                "collection": {"type": "string", "default": "default"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "quality_check",
        "description": "生成知识库质量报告（集合分布、分块统计等）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "default": "default"},
            },
        },
    },
    {
        "name": "convert_file",
        "description": "把单个文档转换为 Markdown / JSON / TXT / HTML 文本，返回转换后的内容。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "输入文件绝对路径。"},
                "output_format": {"type": "string", "default": "md", "enum": list(SUPPORTED_FORMATS)},
            },
            "required": ["input_path"],
        },
    },
    {
        "name": "reindex",
        "description": "重建指定集合的向量索引（删除现有向量，用当前嵌入模型重新嵌入）。返回 job_id 供轮询。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "default": "default"},
                "model": {"type": "string", "description": "可选，切换嵌入模型。"},
            },
        },
    },
    {
        "name": "chat",
        "description": "RAG 对话问答：从知识库检索相关文档，结合多轮对话上下文，调用大模型生成回答并标注引用来源。需要先配置 LLM（DOC2MIND_LLM_PROVIDER + 相关密钥）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户的问题。"},
                    "collection": {"type": "string", "default": "default", "description": "检索的集合名称，default 表示默认集合。"},
                    "collections": {"type": "array", "items": {"type": "string"}, "description": "多选知识库集合名列表，优先于 collection。"},
                    "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20, "description": "检索引用的文档片段数量。"},
                    "chat_id": {"type": "string", "description": "会话 ID（多轮对话时传同一值，不传则新建会话）。"},
                },
                "required": ["query"],
            },
    },
    {
        "name": "curate",
        "description": "AI 整理知识库：enrich（生成标题/摘要/标签）、categorize（自动归类，必要时新建集合）、dedup（语义去重）、consolidate（把小而散的经验笔记归纳成蒸馏笔记）、extract（抽取实体与关系图谱）。dry_run=true（默认）只读预览、零写入；删除/合并类动作确认预览后用 dry_run=false 执行。需要配置 LLM。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "目标集合名，省略则整理全部集合。"},
                "actions": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["enrich", "categorize", "dedup", "consolidate", "extract"]},
                    "description": "要执行的动作，省略则全部五项。",
                },
                "dry_run": {"type": "boolean", "default": True, "description": "true=只读预览（零写入，推荐先跑）；false=执行（含删除/合并/图谱落库）。"},
                "top_k": {"type": "integer", "default": 10, "minimum": 1, "maximum": 200, "description": "enrich/categorize/extract 处理的文档数上限（控制 LLM 调用成本）。"},
            },
        },
    },
    {
        "name": "graph_get",
        "description": "查询知识图谱中的实体与关系数据（节点与边），支持指定集合或全量检索。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "集合名，省略则查全部集合。"},
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500, "description": "返回节点数量上限。"},
            },
        },
    },
    {
        "name": "create_artifact",
        "description": "多格式知识创作导出：将知识内容、研报、教案、表格编译生成物理 PPTX / DOCX / XLSX / HTML 文件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Markdown 或 Artifact 语法内容。"},
                "format": {"type": "string", "enum": ["pptx", "docx", "xlsx", "html", "md"], "default": "docx", "description": "目标导出格式。"},
                "title": {"type": "string", "description": "交付物标题（可选）。"},
                "theme": {"type": "string", "enum": ["tech_blue", "emerald_green", "modern_purple", "warm_orange", "dark_elegant"], "default": "tech_blue", "description": "演示文稿企业主题配色。"},
                "output_path": {"type": "string", "description": "目标输出物理文件绝对路径（可选）。"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "inspect_artifact",
        "description": "对 PPT 演示文稿大纲进行效果自检、健康度体检评分 (0-100分) 与排版/文字密度/演讲准备度诊断。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Markdown 或 Marp 语法幻灯片内容。"},
            },
            "required": ["content"],
        },
    },
]


# --- JSON-RPC 响应辅助 ---
def _ok(result: Any) -> str:
    return json.dumps({"result": result}, ensure_ascii=False)


def _error(code: str, message: str) -> str:
    return json.dumps(
        {"error": {"code": code, "message": message}},
        ensure_ascii=False,
    )


# --- MCP Server 主循环 ---
async def _handle(request: dict[str, Any]) -> dict[str, Any]:
    """处理单个 JSON-RPC 请求。"""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        from doc2mind import __version__
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "doc2mind", "version": __version__},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS_SCHEMA},
        }

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {})
        try:
            # 在事件循环线程外执行工具：embedder 初始化（import onnxruntime/numpy）
            # 与耗时摄入/检索会阻塞事件循环线程（Windows Proactor 下 numpy C 扩展
            # DLL 加载卡死、且工具执行期间 get_job 轮询无法响应），
            # 用 to_thread 挪到工作线程，与 HTTP server 的 asyncio.to_thread 一致。
            result_text = await asyncio.to_thread(_dispatch_tool, name, args)
            parsed = json.loads(result_text)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": "error" in parsed,
                },
            }
        except Exception as e:  # noqa: BLE001
            err_text = _error("INTERNAL", f"工具调用失败: {e}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": err_text}],
                    "isError": True,
                },
            }

    # 未知方法
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"方法未实现: {method}"},
    }


def _dispatch_tool(name: str, args: dict[str, Any]) -> str:
    """按工具名分派到实现。"""
    handlers = {
        "ingest": _tool_ingest,
        "ingest_text": _tool_ingest_text,
        "ingest_job": _tool_ingest_job,
        "get_job": _tool_get_job,
        "search": _tool_search,
        "list_docs": _tool_list_docs,
        "remove_doc": _tool_remove_doc,
        "quality_check": _tool_quality_check,
        "convert_file": _tool_convert_file,
        "reindex": _tool_reindex,
        "chat": _tool_chat,
        "curate": _tool_curate,
        "graph_get": _tool_graph_get,
        "create_artifact": _tool_create_artifact,
        "inspect_artifact": _tool_inspect_artifact,
    }
    handler = handlers.get(name)
    if handler is None:
        return _error("BAD_REQUEST", f"未知工具: {name}")
    try:
        return handler(**args)
    except Exception as e:  # noqa: BLE001 — 工具异常透传（含嵌入/下载的引导信息）
        return _error("TOOL_ERROR", f"{name} 调用失败: {e}")


def run_mcp_server() -> None:
    """MCP Server 主入口（stdio 传输）。

    读取 stdin 的 JSON-RPC 请求，写出响应到 stdout。
    错误/日志走 stderr。

    实现说明：不用 `asyncio.connect_read_pipe` 读 stdin —— Windows 的
    Proactor 事件循环不支持匿名管道（CreateIoCompletionPort 对管道失败），
    会导致 MCP Server 收不到任何请求。这里用后台线程阻塞读 stdin 行，
    把请求放进队列，事件循环从队列取；跨平台可用。

    诊断：设置环境变量 `DOC2MIND_MCP_DUMP_SEC=<秒>` 时，在指定秒数后
    dump 所有线程堆栈到 stderr 并退出（默认关闭，无副作用）。
    """
    import os
    import queue
    import sys
    import threading

    # 日志落盘（数据目录 logs/）；stdio 传输下 stdout 被 JSON-RPC 占用，
    # 文件日志是 MCP 排障的唯一可靠线索
    from doc2mind.core.logging_setup import setup_logging

    setup_logging()

    _dump_sec = os.environ.get("DOC2MIND_MCP_DUMP_SEC")
    if _dump_sec:
        import faulthandler
        try:
            faulthandler.dump_traceback_later(float(_dump_sec), exit=True, file=sys.stderr)
        except Exception:  # noqa: BLE001 — 诊断钩子失败不影响主流程
            pass

    req_queue: queue.Queue[str | None] = queue.Queue()

    # 预热：主线程提前触发 embedder 构造（FastEmbedEmbedder.__init__ 里
    # _select_providers() 会 import onnxruntime/numpy）。若留到首个工具调用
    # 时在后台 _reader 线程竞争下惰性加载，Windows 上 numpy C 扩展 DLL 加载
    # 可能卡死（faulthandler 观测到 create_module 挂起）。预热后 import 缓存
    # 命中，工具调用不再触发 DLL 加载。
    try:
        from doc2mind.core.config import get_settings
        from doc2mind.core.embedder import get_embedder

        _preheat = get_embedder(get_settings())
        _ = _preheat.dimension
    except Exception as e:  # noqa: BLE001 — 预热失败不阻塞启动，工具调用时再试
        sys.stderr.write(f"[doc2mind] embedder 预热失败: {e}\n")

    # 首次使用引导：模型未下载时提示（stderr，避免污染 stdout 的 MCP 协议）
    try:
        from doc2mind.core.embedder.fastembed_impl import first_run_hint

        _hint = first_run_hint()
        if _hint:
            sys.stderr.write(f"[doc2mind] {_hint}\n")
    except Exception:  # noqa: BLE001 — 提示失败不影响启动
        pass

    def _reader() -> None:
        """后台线程：逐行读 stdin，EOF 时放 None 标记退出。"""
        try:
            for line in sys.stdin:
                line = line.strip()
                if line:
                    req_queue.put(line)
        finally:
            req_queue.put(None)

    threading.Thread(target=_reader, daemon=True, name="mcp-stdin").start()

    async def _main() -> None:
        while True:
            line = req_queue.get()
            if line is None:
                break
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                sys.stderr.write(f"无效 JSON: {line[:200]}\n")
                continue
            try:
                response = await _handle(request)
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"处理请求失败: {e}\n")

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
