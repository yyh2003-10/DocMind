"""Agent 模式流式编排（P1）。

服务端编排型 agent：按 planner 选定工具 → LoopController 执行 → 轨迹 SSE →
把工具结果注入上下文 → 流式生成最终回答。

本阶段**不依赖** provider 原生 tool-calling（兼容弱模型）；模型决策来自
既有 planner，执行与权限走 runtime。后续里程碑可把 model_fn 换成真 tool 协议。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

from doc2mind.core.agent.planner import plan_with_llm, user_facing_plan_reason
from doc2mind.core.agent.planner import TOOLS as AGENT_TOOLS
from doc2mind.core.agent.prompt_policy import (
    PROMPT_TRACK_DELIVERY,
    apply_prompt_track,
    boost_max_tokens,
    done_frame_extras,
    resolve_prompt_track,
)
from doc2mind.core.agent.runtime.executors import bind_runtime_executors
from doc2mind.core.agent.runtime.loop import LoopBudget, LoopController, MockModelTurn
from doc2mind.core.agent.runtime.permissions import PermissionGate
from doc2mind.core.agent.runtime.registry import ToolRegistry, builtin_tool_specs
from doc2mind.core.agent.runtime.types import ToolCall, ToolStatus
from doc2mind.core.agent.runtime.workspace import Workspace
from doc2mind.core.config import Settings, get_settings
from doc2mind.core.llm import LLMError, get_llm_client
from doc2mind.core.llm.output import AnswerGuard, OutputSanitizer, ThinkingMetaFilter
from doc2mind.core.rag import RagError
from doc2mind.core.rag import (
    _SUBJECT_ANCHOR,
    _append_turn,
    _cap_history,
    _load_history,
    _max_history,
    _model_spec_payload,
    _resolve_effective_max_tokens,
    _truncate_history_by_token_budget,
    is_weak_model,
)
from doc2mind.core.retriever.search import Retriever

logger = logging.getLogger(__name__)

AGENTS_SYSTEM_BASE = (
    _SUBJECT_ANCHOR
    + "你是 DocMind 的 Agent 模式助手。系统已在你回答前执行了规划工具链，"
    "工具结果以【工具轨迹】形式写在上下文中。\n"
    "硬规则：\n"
    "1. 优先依据【工具轨迹】与资料作答，关键事实标注来源编号（仅限资料列表）；\n"
    "2. 工具未命中时明确说明，禁止编造编号；\n"
    "3. 正文禁止输出 JSON 工具调用或 function_call；\n"
    "4. 交付型任务按结构完整展开，不人为压成短答。\n"
)


def _workspace_root_for(settings: Settings, chat_id: str) -> Path:
    # chat_id 参与路径拼接：必须消毒，防止 ../ 等穿越出 workspaces 根
    raw = (chat_id or "").strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_.")[:64].strip("._")
    if not safe or safe in {".", ".."}:
        safe = "chat-unknown"
    base = getattr(settings, "db_path", None)
    if base:
        root = Path(base).expanduser().parent / "workspaces" / safe
    else:
        root = Path.home() / ".doc2mind" / "workspaces" / safe
    return root


def _sanitize_chat_id_for_fs(chat_id: str) -> str:
    raw = (chat_id or "").strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_.")[:64].strip("._")
    return safe or "chat-unknown"


def _kb_hits_to_source_dicts(loop_result: Any) -> list[dict[str, Any]]:
    """把 kb_search 工具结果转成与 RAG sources 兼容的精简结构，供前端角标/列表使用。"""
    out: list[dict[str, Any]] = []
    idx = 0
    for r in loop_result.tool_results:
        if r.tool_id != "kb_search" or r.status != ToolStatus.OK:
            continue
        for h in (r.data or {}).get("hits") or []:
            idx += 1
            out.append(
                {
                    "index": idx,
                    "source": h.get("source") or "-",
                    "chunk_id": None,
                    "format": "",
                    "page": None,
                    "heading": h.get("heading"),
                    "score": h.get("score") or 0.0,
                    "score_type": "rerank",
                    "confidence_label": "",
                    "source_type": "local",
                    "url": None,
                    "title": h.get("source"),
                    "snippet": (h.get("content") or "")[:280],
                    "source_name": None,
                    "domain": None,
                    "published_at": None,
                    "content_fetched": False,
                    "corroborated_by": 0,
                    "evidence_level": "单一来源",
                }
            )
    return out



def _planner_tools_to_calls(agent_plan: Any, query: str, top_k: int) -> list[ToolCall]:
    """把 planner 工具列表映射为 runtime ToolCall（P1 可执行子集）。"""
    calls: list[ToolCall] = []
    enabled = list(getattr(agent_plan, "enabled_tools", []) or [])
    # knowledge_base → kb_search
    if "knowledge_base" in enabled or not enabled:
        calls.append(
            ToolCall(
                call_id="call_kb_1",
                tool_id="kb_search",
                arguments={"query": query, "top_k": max(1, min(int(top_k or 5), 20))},
            )
        )
    # create_artifact：P1 先不在此步导出，最终正文生成后再按意图导出
    return calls


def _tool_results_to_context_block(results: list[Any]) -> str:
    lines: list[str] = []
    for r in results:
        if r.tool_id == "kb_search" and r.status == ToolStatus.OK:
            hits = (r.data or {}).get("hits") or []
            for i, h in enumerate(hits, 1):
                src = h.get("source") or "-"
                content = (h.get("content") or "").strip()
                lines.append(f"[{i}] 《{src}》\n{content}")
        elif r.status == ToolStatus.OK and r.summary:
            lines.append(f"（{r.tool_id}）{r.summary[:400]}")
        elif r.status != ToolStatus.OK:
            lines.append(f"（{r.tool_id} 失败）{r.error or r.status.value}")
    return "\n\n".join(lines)


def agent_answer_stream(
    query: str,
    collection: str | None = "default",
    top_k: int | None = None,
    chat_id: str | None = None,
    settings: Settings | None = None,
    llm_client: Any | None = None,
    collections: list[str] | None = None,
    model_override: str | None = None,
    enable_web_search: bool = False,
    store: Any | None = None,
    embedder: Any | None = None,
    stop_event: Any | None = None,
    persona: str | None = None,
    persona_prompt: str | None = None,
    attachments: list[str] | None = None,
    memory_context: str | None = None,
    response_mode: str | None = None,
    top_k_override: int | None = None,
) -> Iterator[str]:
    """Agent 模式流式问答（SSE JSON 行，与 rag_answer_stream 帧兼容 + tool_* 扩展）。"""
    s = settings or get_settings()
    t0 = time.perf_counter()
    if model_override and not llm_client:
        s = dc_replace(s, llm_model=model_override.strip())

    cid, history = _load_history(chat_id, s.db_path)

    try:
        client = llm_client or get_llm_client(s)
    except LLMError as e:
        raise RagError(f"LLM 配置错误: {e}") from e
    if client is None:
        raise RagError("未配置 LLM，无法进入 Agent 模式")

    from doc2mind.core.llm.model_registry import get_model_spec

    model_spec = get_model_spec(client.model_name, client.provider)
    slim = is_weak_model(client.model_name)
    top_k = top_k_override or top_k or getattr(s, "rag_top_k", 5) or 5

    yield json.dumps(
        {"type": "status", "message": "Agent 模式：规划与工具执行中…"},
        ensure_ascii=False,
    )

    agent_plan = plan_with_llm(query, client, history, settings=s)
    prompt_track = resolve_prompt_track(
        query_type=getattr(agent_plan, "query_type", None),
        creative_mode=getattr(agent_plan, "creative_mode", None),
        explicit=response_mode,
    )
    # Agent 模式默认交付轨更利于长文/任务输出
    if prompt_track != PROMPT_TRACK_DELIVERY and getattr(agent_plan, "query_type", "") in (
        "creative",
        "research",
        "task",
        "troubleshoot",
    ):
        prompt_track = PROMPT_TRACK_DELIVERY

    tool_names = [
        AGENT_TOOLS[t]["name"] for t in getattr(agent_plan, "enabled_tools", []) if t in AGENT_TOOLS
    ]
    plan_reason = user_facing_plan_reason(agent_plan, tool_names)
    yield json.dumps(
        {
            "type": "thinking",
            "text": f"{plan_reason}\n\n（Agent 模式 · 轨迹可展开）",
            "persona": persona,
            "prompt_track": prompt_track,
        },
        ensure_ascii=False,
    )
    yield json.dumps(
        {
            "type": "agent_plan",
            "query_type": getattr(agent_plan, "query_type", None),
            "planned_tools": list(getattr(agent_plan, "enabled_tools", []) or []),
            "prompt_track": prompt_track,
            "mode": "agent",
        },
        ensure_ascii=False,
    )

    # 工作区 + 注册表
    ws_root = _workspace_root_for(s, cid)
    workspace = Workspace(ws_root)
    registry = ToolRegistry(builtin_tool_specs())

    def _search_fn(q: str, collection=None, top_k=5):
        if store is None or embedder is None:
            return [], None
        try:
            from doc2mind.core.reranker import get_reranker

            reranker = get_reranker(s)
        except Exception:  # noqa: BLE001
            reranker = None
        try:
            retriever = Retriever(store, embedder, reranker=reranker)
            coll = collections if collections else collection
            return retriever.search(q, collection=coll, top_k=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent kb_search 失败: %s", exc)
            return [], None

    write_policy = str(getattr(s, "agent_file_write_policy", "session_allow") or "session_allow")
    if write_policy not in ("ask", "session_allow", "always_allow_workspace"):
        write_policy = "session_allow"
    # Agent 模式 P1：工具链内写入默认会话授权，避免无 UI 确认时全部 denied
    gate = PermissionGate(write_policy=write_policy)  # type: ignore[arg-type]
    auto_approve = write_policy != "ask"

    bind_runtime_executors(
        registry,
        workspace=workspace,
        search_fn=_search_fn if store is not None else None,
        collection=collections if collections else collection,
    )

    planned_calls = _planner_tools_to_calls(agent_plan, query, top_k)
    if not planned_calls:
        # 兜底：至少做一次知识库检索，保证 Agent 模式有轨迹
        planned_calls = [
            ToolCall(call_id="call_kb_fallback", tool_id="kb_search", arguments={"query": query, "top_k": top_k})
        ]

    # T8：provider 原生 tool-calling（OpenAI 兼容）；弱模型/关闭时保持编排降级
    use_native_tools = bool(
        getattr(s, "agent_native_tool_calling", True)
        and getattr(client, "supports_tool_calling", False)
        and not getattr(agent_plan, "degraded", False)
    )

    # SSE 转发 tool 轨迹（先创建列表，再挂回调，避免 NameError）
    _pending_events: list[tuple[str, dict[str, Any]]] = []
    controller_box: dict[str, Any] = {"controller": None}

    def on_event(name: str, payload: dict[str, Any]) -> None:
        _pending_events.append((name, payload))
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            ctrl = controller_box.get("controller")
            if ctrl is not None:
                ctrl.cancel()

    controller = LoopController(
        registry,
        gate=gate,
        budget=LoopBudget(max_steps=6, max_tool_calls=12, max_wall_ms=60_000),
        on_event=on_event,
    )
    controller_box["controller"] = controller

    step_state = {"i": 0, "native": use_native_tools, "messages": [{"role": "user", "content": query}]}

    # 自研 tool schema：仅白名单检索/工作区能力，禁止任意 shell
    native_tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": "kb_search",
                "description": "在本地知识库检索相关切片",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                },
            },
        }
    ] if use_native_tools else []

    def model_fn(_messages: list[dict[str, Any]], step: int) -> MockModelTurn:
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            controller.cancel()
        if not step_state["native"]:
            # 编排降级：规划器选中的工具一次性执行
            if step_state["i"] == 0:
                step_state["i"] = 1
                return MockModelTurn(tool_calls=list(planned_calls))
            return MockModelTurn(final_text="")
        # 真 tool-calling：loop 已把 tool 结果回注 messages
        try:
            turn = client.chat_with_tools(
                list(_messages),
                tools=native_tool_schemas,
                max_tokens=getattr(s, "llm_max_tokens", 2048),
            )
        except Exception as ex:  # noqa: BLE001 —— 降级编排，不阻断对话
            logger.warning("原生 tool-calling 失败，降级为服务端编排: %s", ex)
            step_state["native"] = False
            if step_state["i"] == 0:
                step_state["i"] = 1
                return MockModelTurn(tool_calls=list(planned_calls))
            return MockModelTurn(final_text="")
        if turn.wants_tools:
            mapped: list[ToolCall] = []
            for c in turn.tool_calls:
                tool_id = "kb_search" if c.name == "kb_search" else c.name
                if tool_id not in ("kb_search", "workspace_list"):
                    # 白名单外：忽略，防止任意工具
                    continue
                mapped.append(ToolCall(call_id=c.id, tool_id=tool_id, arguments=c.arguments))
            if not mapped:
                return MockModelTurn(final_text=turn.final_text or "（模型未选择可用工具）")
            return MockModelTurn(tool_calls=mapped)
        return MockModelTurn(final_text=turn.final_text or "")

    if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
        yield json.dumps({"type": "status", "message": "已停止"}, ensure_ascii=False)
        yield json.dumps(
            {
                "done": True,
                "chat_id": cid,
                "model": client.model_name,
                "provider": client.provider,
                "partial": True,
                "warning": "已停止生成。",
                "sources": [],
                **done_frame_extras(track=prompt_track, truncated=False),
                "mode": "agent",
            },
            ensure_ascii=False,
        )
        return

    loop_result = controller.run(
        model_fn,
        seed_messages=[{"role": "user", "content": query}],
        auto_approve_session=auto_approve,
    )
    # 冲刷 loop 事件
    for name, payload in _pending_events:
        if name == "tool_call":
            yield json.dumps({"type": "tool_call", **payload}, ensure_ascii=False)
        elif name == "tool_result":
            # 补全 summary/status
            matched = next(
                (r for r in loop_result.tool_results if r.call_id == payload.get("call_id")),
                None,
            )
            frame = {
                "type": "tool_result",
                **payload,
                "summary": (matched.summary[:300] if matched and matched.summary else payload.get("summary", "")),
                "error": (matched.error if matched else None),
            }
            yield json.dumps(frame, ensure_ascii=False)
        elif name == "permission_request":
            yield json.dumps({"type": "permission_request", **payload}, ensure_ascii=False)

    tool_block = _tool_results_to_context_block(loop_result.tool_results)
    denied = loop_result.denied
    if denied:
        yield json.dumps(
            {"type": "status", "message": f"部分工具未授权: {', '.join(sorted(set(denied)))}"},
            ensure_ascii=False,
        )

    # 用户在工具阶段已停止：不再调 LLM，直接终帧
    if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
        stopped = True
        yield json.dumps(
            {
                "done": True,
                "chat_id": cid,
                "model": client.model_name,
                "provider": client.provider,
                "partial": True,
                "warning": "已停止生成。",
                "sources": _kb_hits_to_source_dicts(loop_result),
                "mode": "agent",
                "tools_used": [c.tool_id for c in loop_result.tool_calls],
                **done_frame_extras(track=prompt_track, truncated=False),
            },
            ensure_ascii=False,
        )
        return

    # 组装最终生成上下文
    prompt_track_final = prompt_track
    system_prompt = apply_prompt_track(AGENTS_SYSTEM_BASE, prompt_track_final)
    if persona and not slim:
        system_prompt = f"【当前角色：{persona}】\n" + system_prompt
    if persona_prompt and persona_prompt.strip():
        system_prompt = f"【当前角色：自定义】{persona_prompt.strip().splitlines()[0]}\n" + system_prompt
    if memory_context and memory_context.strip():
        system_prompt += (
            "\n\n【用户记忆（仅偏好参考）】\n"
            + memory_context.strip()
            + "\n请勿把记忆主题当作本轮问题。\n"
        )

    truncated_history = _cap_history(
        _truncate_history_by_token_budget(history, s.rag_max_history_tokens, s.chars_per_token),
        _max_history(s),
    )
    user_content = query
    if tool_block:
        user_content = (
            f"【工具轨迹】\n{tool_block}\n\n---\n请基于以上工具结果与资料回答：{query}"
        )
    else:
        user_content = f"（本轮工具未返回有效资料）\n请基于通用知识谨慎回答：{query}"

    messages = [
        {"role": "system", "content": system_prompt},
        *truncated_history,
        {"role": "user", "content": user_content},
    ]

    yield json.dumps({"type": "status", "message": "正在生成 Agent 回答…"}, ensure_ascii=False)

    llm_timeout = s.llm_timeout if s.llm_timeout > 0 else None
    effective_max_tokens, _src = _resolve_effective_max_tokens(
        model_name=client.model_name,
        provider=client.provider,
        user_config=s.llm_max_tokens,
        registry_spec=model_spec,
        client=client,
        prompt_track=prompt_track_final,
    )
    if effective_max_tokens is None:
        effective_max_tokens = boost_max_tokens(8192, prompt_track_final)

    collected: list[str] = []
    output_filter = OutputSanitizer()
    thinking_filter = ThinkingMetaFilter()
    answer_guard = AnswerGuard()
    stopped = False
    stream_error: Exception | None = None
    try:
        for kind, token in client.stream_chat_tagged(
            messages,
            max_tokens=effective_max_tokens,
            timeout=llm_timeout,
            stop_event=stop_event,
        ):
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            if kind == "thinking":
                visible = thinking_filter.feed(token)
                if visible:
                    yield json.dumps({"type": "thinking", "text": visible}, ensure_ascii=False)
                continue
            visible = output_filter.feed(token)
            if not visible:
                continue
            guarded = answer_guard.feed(visible)
            if answer_guard.should_abort:
                break
            if guarded:
                collected.append(guarded)
                yield json.dumps({"token": guarded}, ensure_ascii=False)
        else:
            # 正常结束：必须同时 flush 句子缓冲与完整性守卫缓冲
            # （否则末段无句读的 token 会永远卡在 OutputSanitizer 里，正文为空）
            visible_tail = output_filter.flush()
            if visible_tail:
                guarded_tail = answer_guard.feed(visible_tail)
                if not answer_guard.should_abort and guarded_tail:
                    collected.append(guarded_tail)
                    yield json.dumps({"token": guarded_tail}, ensure_ascii=False)
            guard_tail = answer_guard.flush()
            if not answer_guard.should_abort and guard_tail:
                collected.append(guard_tail)
                yield json.dumps({"token": guard_tail}, ensure_ascii=False)
        # break 路径（取消/守卫中断）也要冲刷已缓冲正文，避免“有生成却无 token”
        if not collected:
            visible_tail = output_filter.flush()
            if visible_tail and not answer_guard.should_abort:
                collected.append(visible_tail)
                yield json.dumps({"token": visible_tail}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        stream_error = exc
        logger.exception("Agent 模式生成失败")
        # 异常时尽量保留已缓冲内容
        try:
            visible_tail = output_filter.flush()
            if visible_tail:
                collected.append(visible_tail)
                yield json.dumps({"token": visible_tail}, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass


    reply = "".join(collected)
    truncated = bool(getattr(client, "last_truncated", False))
    source_dicts = _kb_hits_to_source_dicts(loop_result)

    # 交付型：把正文写入工作区 notes
    # 注意：write_policy=ask 时 loop 内写入会被拒，这里也不得绕过权限静默落盘
    artifact_paths: list[dict[str, Any]] = []
    allow_workspace_write = auto_approve or write_policy == "always_allow_workspace"
    if reply.strip() and not stopped and stream_error is None and allow_workspace_write:
        try:
            note_rel = f"notes/agent_{_sanitize_chat_id_for_fs(cid)}_{int(t0)}.md"
            out = workspace.write_text(note_rel, reply)
            artifact_paths.append(
                {
                    "artifact_id": f"note_{cid}",
                    "format": "md",
                    "file_path": str(out),
                    "relative_path": note_rel,
                    "title": "Agent 回答笔记",
                }
            )
            yield json.dumps(
                {
                    "type": "artifact_ready",
                    "artifact_id": f"note_{cid}",
                    "format": "md",
                    "file_path": str(out),
                    "file_name": out.name,
                },
                ensure_ascii=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent 笔记写入失败: %s", exc)

        if (
            getattr(agent_plan, "query_type", "") == "creative"
            and not slim
            and reply.strip()
        ):
            try:
                from doc2mind.core.creator import export_artifact as real_export

                fmt = "docx"
                mode = (getattr(agent_plan, "creative_mode", "") or "").lower()
                if mode == "ppt":
                    fmt = "pptx"
                elif mode in ("table",):
                    fmt = "xlsx"
                elif mode in ("web",):
                    fmt = "html"
                exp_path = workspace.root / "artifacts" / f"agent_export_{int(t0)}.{fmt}"
                res = real_export(
                    content=reply,
                    target_format=fmt,
                    output_path=str(exp_path),
                )
                if getattr(res, "ok", False):
                    artifact_paths.append(
                        {
                            "artifact_id": f"export_{cid}",
                            "format": fmt,
                            "file_path": str(getattr(res, "file_path", exp_path)),
                            "title": getattr(res, "file_name", exp_path.name),
                        }
                    )
                    yield json.dumps(
                        {
                            "type": "artifact_ready",
                            "artifact_id": f"export_{cid}",
                            "format": fmt,
                            "file_path": str(getattr(res, "file_path", exp_path)),
                            "file_name": getattr(res, "file_name", exp_path.name),
                        },
                        ensure_ascii=False,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Agent 导出失败: %s", exc)

        _append_turn(cid, query, reply, s.db_path, sources=None)
    elif reply.strip() and not stopped and stream_error is None:
        # 未授权写工作区：仍保存会话历史
        _append_turn(cid, query, reply, s.db_path, sources=None)

    elapsed = int((time.perf_counter() - t0) * 1000)
    if truncated:
        yield json.dumps(
            {
                "type": "status",
                "message": "⚠ Agent 回答可能因输出上限被截断，可继续写或调大输出上限",
            },
            ensure_ascii=False,
        )

    done_payload: dict[str, Any] = {
        "done": True,
        "chat_id": cid,
        "model": client.model_name,
        "provider": client.provider,
        "persona": persona,
        "model_spec": _model_spec_payload(model_spec),
        "total_chunks": sum(
            1
            for r in loop_result.tool_results
            if r.tool_id == "kb_search" and r.status == ToolStatus.OK
            for _ in ((r.data or {}).get("hits") or [])
        ),
        "elapsed_ms": elapsed,
        "partial": stream_error is not None or stopped or truncated,
        "sources": source_dicts,
        "mode": "agent",
        "steps": loop_result.steps,
        "tools_used": [c.tool_id for c in loop_result.tool_calls],
        "artifacts": artifact_paths,
        "workspace_root": str(workspace.root),
        "trajectory_warning": loop_result.warning,
        **done_frame_extras(
            track=prompt_track_final,
            truncated=truncated,
            continue_writing=False,
        ),
    }
    if stream_error is not None:
        done_payload["warning"] = f"Agent 生成失败: {stream_error}"
    elif stopped:
        done_payload["warning"] = "已停止生成。"
    elif truncated:
        done_payload["warning"] = "回答可能被输出上限截断。"

    yield json.dumps(done_payload, ensure_ascii=False)
