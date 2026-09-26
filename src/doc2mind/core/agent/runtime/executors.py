"""P1：把 ToolRegistry 执行器接到既有 DocMind 能力。

依赖全部注入，便于单测；不在此处打开全局 DB/网络。
复用：Retriever.search / creator.export_artifact / Workspace。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def make_web_search_executor(
    web_search_fn: Callable[..., Any],
    *,
    mode: str = "normal",
    github_token: str | None = None,
    max_results: int | None = None,
) -> Callable[[ToolCall], ToolResult]:
    """web_search：可多轮调用的联网检索工具（借鉴 coding-agent 工具循环形态）。

    web_search_fn(query, mode=..., max_results=..., github_token=...) -> 结果列表
    或 WebSearchService.search 的兼容返回。执行器把结果压成可引用摘要 + 结构化 data，
    供 Loop 回注 messages 与最终 sources 组装。
    """

    def web_search(call: ToolCall) -> ToolResult:
        query = str(call.arguments.get("query") or "").strip()
        if not query:
            return _err(call, "query 不能为空")
        call_mode = str(call.arguments.get("mode") or mode or "normal").strip().lower()
        if call_mode not in ("normal", "deep"):
            call_mode = mode if mode in ("normal", "deep") else "normal"
        arg_max = call.arguments.get("max_results")
        try:
            max_n = int(arg_max) if arg_max is not None else (max_results or (16 if call_mode == "deep" else 8))
        except (TypeError, ValueError):
            max_n = 16 if call_mode == "deep" else 8
        max_n = max(1, min(max_n, 32))
        try:
            raw = web_search_fn(
                query,
                mode=call_mode,
                max_results=max_n,
                github_token=call.arguments.get("github_token") or github_token,
            )
        except TypeError:
            try:
                raw = web_search_fn(query)
            except Exception as exc:  # noqa: BLE001
                return _err(call, f"联网搜索失败: {exc}")
        except Exception as exc:  # noqa: BLE001
            return _err(call, f"联网搜索失败: {exc}")

        if isinstance(raw, tuple) and raw:
            raw = raw[0]
        if raw is None:
            raw = []
        items: list[dict[str, Any]] = []
        for r in list(raw)[:max_n]:
            items.append(
                {
                    "title": getattr(r, "title", None) or (r.get("title") if isinstance(r, dict) else None) or "-",
                    "url": getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None) or "",
                    "snippet": getattr(r, "snippet", None)
                    or (r.get("snippet") if isinstance(r, dict) else "")
                    or "",
                    "content": (
                        getattr(r, "content", None)
                        or (r.get("content") if isinstance(r, dict) else "")
                        or ""
                    )[:1200],
                    "domain": getattr(r, "domain", None) or (r.get("domain") if isinstance(r, dict) else "") or "",
                    "source_name": getattr(r, "source_name", None)
                    or (r.get("source_name") if isinstance(r, dict) else None)
                    or "Web",
                    "relevance_score": float(
                        getattr(r, "relevance_score", None)
                        or (r.get("relevance_score") if isinstance(r, dict) else 0.0)
                        or 0.0
                    ),
                    "evidence_level": getattr(r, "evidence_level", None)
                    or (r.get("evidence_level") if isinstance(r, dict) else None)
                    or "单一来源",
                    "content_fetched": bool(
                        getattr(r, "content_fetched", None)
                        if getattr(r, "content_fetched", None) is not None
                        else (
                            r.get("content_fetched")
                            if isinstance(r, dict)
                            else bool(getattr(r, "content", None) or (r.get("content") if isinstance(r, dict) else None))
                        )
                    ),
                    "published_at": getattr(r, "published_at", None)
                    or (r.get("published_at") if isinstance(r, dict) else None),
                    "corroborated_by": int(
                        getattr(r, "corroborated_by", None)
                        or (r.get("corroborated_by") if isinstance(r, dict) else 0)
                        or 0
                    ),
                }
            )
        fetched = sum(1 for it in items if it.get("content_fetched") and (it.get("content") or "").strip())
        if not items:
            summary = "联网搜索无可用结果"
        else:
            heads = [f"《{(it.get('title') or '')[:20]}》" for it in items[:3]]
            summary = (
                f"联网检索 {len(items)} 条（mode={call_mode}，已精读 {fetched}）："
                + "、".join(heads)
            )
        return _ok(
            call,
            summary,
            {
                "results": items,
                "count": len(items),
                "fetched_count": fetched,
                "query": query,
                "mode": call_mode,
            },
        )

    return web_search


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
            fn = inspect_fn
            if fn is None:
                from doc2mind.core.creator import inspect_presentation

                fn = inspect_presentation
            report = fn(content)
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
    web_search_fn: Callable[..., Any] | None = None,
    web_search_mode: str = "normal",
    github_token: str | None = None,
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
    if web_search_fn is not None and registry.get("web_search") is not None:
        registry.bind_executor(
            "web_search",
            make_web_search_executor(
                web_search_fn,
                mode=web_search_mode or "normal",
                github_token=github_token,
            ),
        )
    if registry.get("inspect_artifact") is not None:
        registry.bind_executor("inspect_artifact", make_inspect_executor(inspect_fn))
    return registry
