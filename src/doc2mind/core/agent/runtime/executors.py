"""P1：把 ToolRegistry 执行器接到既有 DocMind 能力。

依赖全部注入，便于单测；不在此处打开全局 DB/网络。
复用：Retriever.search / creator.export_artifact / Workspace。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from doc2mind.core.agent.runtime.registry import ToolRegistry, builtin_tool_specs
from doc2mind.core.agent.runtime.types import ToolCall, ToolResult, ToolStatus
from doc2mind.core.agent.runtime.workspace import PathDeniedError, Workspace

logger = logging.getLogger(__name__)

KB_SEARCH_SUMMARY_BUDGET = 2400


def _err(call: ToolCall, message: str) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status=ToolStatus.ERROR,
        error=message,
    )


def _ok(
    call: ToolCall,
    summary: str,
    data: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status=ToolStatus.OK,
        summary=summary,
        data=data or {},
    )


def make_workspace_executors(workspace: Workspace) -> dict[str, Callable[[ToolCall], ToolResult]]:
    """list / read / write 工作区文件。"""

    def list_workspace(call: ToolCall) -> ToolResult:
        sub = str(call.arguments.get("sub") or ".")
        try:
            files = workspace.list_files(sub)
        except PathDeniedError as exc:
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status=ToolStatus.DENIED,
                error=str(exc),
            )
        preview = files[:50]
        return _ok(
            call,
            f"共 {len(files)} 个文件" + (f"，预览前 {len(preview)} 个" if len(files) > 50 else ""),
            {"files": preview, "total": len(files)},
        )

    def read_workspace_file(call: ToolCall) -> ToolResult:
        path = str(call.arguments.get("path") or "")
        try:
            text = workspace.read_text(path)
        except PathDeniedError as exc:
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status=ToolStatus.DENIED,
                error=str(exc),
            )
        except FileNotFoundError:
            return _err(call, f"文件不存在: {path}")
        except Exception as exc:  # noqa: BLE001
            return _err(call, f"读取失败: {exc}")
        return _ok(call, text[:500], {"path": path, "chars": len(text), "text": text})

    def write_workspace_file(call: ToolCall) -> ToolResult:
        path = str(call.arguments.get("path") or "")
        content = call.arguments.get("content")
        if not isinstance(content, str):
            return _err(call, "content 必须是字符串")
        try:
            out = workspace.write_text(path, content)
        except PathDeniedError as exc:
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status=ToolStatus.DENIED,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            return _err(call, f"写入失败: {exc}")
        rel = str(out.relative_to(workspace.root)) if out.is_absolute() else str(out)
        return _ok(call, f"已写入 {rel}（{len(content)} 字）", {"path": rel, "bytes": len(content.encode('utf-8'))})

    return {
        "list_workspace": list_workspace,
        "read_workspace_file": read_workspace_file,
        "write_workspace_file": write_workspace_file,
    }


def make_kb_search_executor(
    search_fn: Callable[..., Any],
    *,
    collection: str | list[str] | None = "default",
) -> Callable[[ToolCall], ToolResult]:
    """kb_search：search_fn(query, collection=..., top_k=...) -> (hits, stats) 或类似。

    hits 元素 duck-typing：需有 content/source/score（或 chunk.source 等）。
    """

    def kb_search(call: ToolCall) -> ToolResult:
        query = str(call.arguments.get("query") or "").strip()
        if not query:
            return _err(call, "query 不能为空")
        top_k = call.arguments.get("top_k")
        try:
            top_k_i = int(top_k) if top_k is not None else 5
        except (TypeError, ValueError):
            top_k_i = 5
        top_k_i = max(1, min(top_k_i, 20))
        try:
            result = search_fn(query, collection=collection, top_k=top_k_i)
        except TypeError:
            try:
                result = search_fn(query)
            except Exception as exc:  # noqa: BLE001
                return _err(call, f"检索失败: {exc}")
        except Exception as exc:  # noqa: BLE001
            return _err(call, f"检索失败: {exc}")

        hits = result[0] if isinstance(result, tuple) and result else result
        if hits is None:
            hits = []
        items: list[dict[str, Any]] = []
        for h in list(hits)[:top_k_i]:
            chunk = getattr(h, "chunk", h)
            items.append(
                {
                    "source": getattr(chunk, "source", None) or getattr(h, "source", None),
                    "heading": getattr(chunk, "heading", None),
                    "score": float(getattr(h, "score", 0.0) or getattr(h, "rerank_score", 0.0) or 0.0),
                    "content": (getattr(chunk, "content", None) or getattr(h, "content", "") or "")[:500],
                }
            )
        summary_parts = [f"[{i+1}] {it.get('source') or '-'}: {(it.get('content') or '')[:80]}" for i, it in enumerate(items)]
        summary = "\n".join(summary_parts)[:KB_SEARCH_SUMMARY_BUDGET] if items else "无命中"
        return _ok(call, summary, {"hits": items, "count": len(items), "query": query})

    return kb_search


def make_export_executor(
    workspace: Workspace,
    export_fn: Callable[..., Any] | None = None,
) -> Callable[[ToolCall], ToolResult]:
    """export_artifact：导出到工作区 artifacts/ 下。export_fn 可注入单测 stub。"""

    def export_artifact(call: ToolCall) -> ToolResult:
        content = call.arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return _err(call, "content 不能为空")
        fmt = str(call.arguments.get("format") or "docx").lower().lstrip(".")
        title = call.arguments.get("title")
        theme = call.arguments.get("theme")
        out_path = workspace.root / "artifacts" / f"artifact_{call.call_id}.{fmt}"
        try:
            if export_fn is None:
                from doc2mind.core.creator import export_artifact as real_export

                export_fn_local = real_export
            else:
                export_fn_local = export_fn
            res = export_fn_local(
                content=content,
                target_format=fmt,
                output_path=str(out_path),
                title_override=str(title) if title else None,
                theme=str(theme) if theme else None,
            )
        except Exception as exc:  # noqa: BLE001
            return _err(call, f"导出失败: {exc}")

        ok = bool(getattr(res, "ok", True))
        file_path = str(getattr(res, "file_path", "") or out_path)
        error = getattr(res, "error", None)
        if not ok:
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status=ToolStatus.ERROR,
                error=str(error or "导出失败"),
                data={"file_path": file_path},
            )
        # 规范化到工作区内相对路径（若实现写到了别处仍回报绝对路径）
        rel = file_path
        try:
            p = Path(file_path).resolve()
            rel = str(p.relative_to(workspace.root.resolve()))
        except Exception:  # noqa: BLE001
            rel = file_path
        return _ok(
            call,
            f"已导出 {fmt}: {rel}",
            {
                "format": fmt,
                "file_path": file_path,
                "relative_path": rel,
                "file_name": getattr(res, "file_name", Path(file_path).name),
            },
        )

    return export_artifact


def make_inspect_executor(inspect_fn: Callable[..., Any] | None = None) -> Callable[[ToolCall], ToolResult]:
    def inspect_artifact(call: ToolCall) -> ToolResult:
        content = call.arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return _err(call, "content 不能为空")
        try:
            if inspect_fn is None:
                from doc2mind.core.creator import inspect_presentation as inspect_fn

            report = inspect_fn(content)
        except Exception as exc:  # noqa: BLE001
            return _err(call, f"体检失败: {exc}")
        score = getattr(report, "score", None)
        grade = getattr(report, "grade", None)
        summary = getattr(report, "summary", "") or f"score={score} grade={grade}"
        return _ok(
            call,
            str(summary)[:500],
            {
                "score": score,
                "grade": grade,
                "slide_count": getattr(report, "slide_count", None),
            },
        )

    return inspect_artifact


def bind_runtime_executors(
    registry: ToolRegistry,
    *,
    workspace: Workspace | None = None,
    search_fn: Callable[..., Any] | None = None,
    export_fn: Callable[..., Any] | None = None,
    inspect_fn: Callable[..., Any] | None = None,
    collection: str | list[str] | None = "default",
) -> ToolRegistry:
    """把可用执行器绑进 registry（缺失依赖的工具保持 unbound，loop 会得到 executor not bound）。"""
    if not registry.list_specs(enabled_only=False):
        for spec in builtin_tool_specs():
            registry.register(spec)

    if workspace is not None:
        for tool_id, executor in make_workspace_executors(workspace).items():
            registry.bind_executor(tool_id, executor)
        registry.bind_executor(
            "export_artifact", make_export_executor(workspace, export_fn=export_fn)
        )
    if search_fn is not None:
        registry.bind_executor(
            "kb_search", make_kb_search_executor(search_fn, collection=collection)
        )
    if registry.get("inspect_artifact") is not None:
        registry.bind_executor("inspect_artifact", make_inspect_executor(inspect_fn))
    return registry
