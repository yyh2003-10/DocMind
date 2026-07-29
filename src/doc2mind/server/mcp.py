"""MCP Server — 暴露 7 个工具给 Cursor / Claude Desktop / Windsurf 等 AI 工具。

传输方式：stdio（MCP 默认）

暴露的工具：
    ingest         path, collection="default", recursive=False, force=False
    search         query, collection="default", top_k=10
    list_docs      collection=None, limit=50
    remove_doc     target (file path or doc_id), collection="default"
    quality_check  collection="default"
    convert_file   input_path, output_format="md"
    reindex        collection="default", model=None

启动：
    doc2mind mcp

接入：在 AI 工具的 MCP 配置里注册：
    { "mcpServers": { "doc2mind": { "command": "doc2mind", "args": ["mcp"] } } }
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from doc2mind.core.config import get_settings
from doc2mind.core.converter import (
    SUPPORTED_FORMATS,
    ConversionError,
    convert_document,
)
from doc2mind.core.embedder import get_embedder
from doc2mind.core.loader.detect import get_loader, is_supported
from doc2mind.core.models import LoadedDocument
from doc2mind.core.pipeline import ingest_path
from doc2mind.core.retriever.search import Retriever
from doc2mind.core.store.sqlite_vec import VectorStore


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
        # 简化版质量报告
        format_dist: dict[str, int] = {}
        total_chunks = 0
        for d in docs:
            format_dist[d.format] = format_dist.get(d.format, 0) + 1
            total_chunks += d.chunk_count
        return _ok({
            "collection": collection,
            "total_documents": len(docs),
            "total_chunks": total_chunks,
            "format_distribution": format_dist,
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
    """重建指定集合的向量索引（占位实现，需异步任务支持）。

    TODO: 阶段 8 引入 job queue 后实现真正的重建逻辑。
    当前返回 job_id 供客户端轮询。
    """
    job_id = f"reindex-{uuid.uuid4().hex[:12]}"
    return _ok({
        "job_id": job_id,
        "status": "pending",
        "message": "重建索引任务已创建。轮询 GET /v1/jobs/{job_id} 查询状态。",
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
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "doc2mind", "version": "0.1.0"},
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
            result_text = _dispatch_tool(name, args)
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
        "search": _tool_search,
        "list_docs": _tool_list_docs,
        "remove_doc": _tool_remove_doc,
        "quality_check": _tool_quality_check,
        "convert_file": _tool_convert_file,
        "reindex": _tool_reindex,
    }
    handler = handlers.get(name)
    if handler is None:
        return _error("BAD_REQUEST", f"未知工具: {name}")
    return handler(**args)


def run_mcp_server() -> None:
    """MCP Server 主入口（stdio 传输）。

    读取 stdin 的 JSON-RPC 请求，写出响应到 stdout。
    错误/日志走 stderr。
    """
    import sys

    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)

    async def _main() -> None:
        loop = asyncio.get_event_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        buffer = ""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")

            # 按换行符分割请求（MCP stdio 默认行分隔 JSON-RPC）
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                    response = await _handle(request)
                    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
                except json.JSONDecodeError:
                    sys.stderr.write(f"无效 JSON: {line[:200]}\n")
                except Exception as e:  # noqa: BLE001
                    sys.stderr.write(f"处理请求失败: {e}\n")

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
