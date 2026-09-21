"""长文大纲/分块编排（T10 / ISSUE-10）。

P2：大纲先行 → 按节填充 drafts → 汇总。不改变 RAG 短答默认路径。
SSE 可发 longform_outline / longform_section / longform_done 帧。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OutlineSection:
    index: int
    title: str
    bullets: list[str] = field(default_factory=list)
    draft: str = ""
    status: str = "pending"  # pending | writing | done | skipped


@dataclass
class LongformPlan:
    query: str
    title: str
    sections: list[OutlineSection] = field(default_factory=list)
    mode: str = "delivery"
    max_sections: int = 8
    max_chars_total: int = 12000
    elapsed_ms: int = 0


@dataclass
class LongformResult:
    plan: LongformPlan
    final_text: str
    status: str  # succeeded | budget_exhausted | cancelled
    sections_done: int = 0
    sections_skipped: int = 0
    warning: str | None = None


_HEADING_RE = re.compile(r"^(?:#{1,6}\s*|第[一二三四五六七八九十0-9]+[章节部分]|Section\s*\d+)\s*(.+)$", re.M)


def parse_outline_from_text(text: str, *, max_sections: int = 8) -> list[OutlineSection]:
    """从模型大纲文本解析章节标题。

    优先匹配 Markdown 标题 / 「第N章」；无标题时按空行段落切。
    """
    titles: list[str] = []
    for m in _HEADING_RE.finditer(text or ""):
        t = m.group(1).strip()
        if t and t not in titles:
            titles.append(t)
    if not titles:
        for line in (text or "").splitlines():
            ls = line.strip().lstrip("-•*").strip()
            if 2 <= len(ls) <= 40 and not ls.startswith("#"):
                titles.append(ls)
            if len(titles) >= max_sections:
                break
    return [OutlineSection(index=i + 1, title=t) for i, t in enumerate(titles[:max_sections])]


def build_default_outline(query: str, *, max_sections: int = 5) -> list[OutlineSection]:
    """LLM 不可用时的规则大纲（保证长任务可继续，不静默失败）。"""
    q = (query or "主题").strip()[:40]
    titles = [
        f"{q}：背景与目标",
        f"{q}：核心要点",
        f"{q}：实施/说明细节",
        f"{q}：风险与注意事项",
        f"{q}：总结与下一步",
    ][: max(2, max_sections)]
    return [OutlineSection(index=i + 1, title=t) for i, t in enumerate(titles)]


def _section_prompt(plan: LongformPlan, section: OutlineSection) -> str:
    bullets = "；".join(section.bullets) if section.bullets else "覆盖标题要点"
    return (
        f"请撰写长文「{plan.title}」的第 {section.index} 节「{section.title}」。\n"
        f"要点提示：{bullets}\n"
        f"要求：中文、成段叙述、与整篇结构衔接；只输出本节正文，不要重复整篇大纲。"
        f"字数约 300-600 字。"
    )


def run_longform(
    query: str,
    *,
    llm_client: Any | None = None,
    settings: Any | None = None,
    max_sections: int = 8,
    max_chars_total: int = 12000,
    max_wall_ms: int = 180_000,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    stop_event: Any | None = None,
    source_context: str = "",
) -> LongformResult:
    """大纲 → 分节生成 → 汇总。

    llm_client.chat(messages) 返回字符串；不可用时每节回退短模板，status=budget/skipped 可见。
    """
    t0 = time.perf_counter()
    emit = on_event or (lambda _n, _p: None)

    def _stopped() -> bool:
        return bool(stop_event is not None and getattr(stop_event, "is_set", lambda: False)())

    def _chat(messages: list[dict[str, str]], fallback: str) -> str:
        if llm_client is None:
            return fallback
        try:
            text = llm_client.chat(messages)
            return (text or "").strip() or fallback
        except Exception as ex:  # noqa: BLE001
            logger.debug("longform LLM 失败，使用模板: %s", ex)
            return fallback

    # 1) 大纲
    emit("longform_outline_start", {"query": query})
    outline_text = _chat(
        [
            {
                "role": "system",
                "content": "你是长文大纲助手。输出 3-8 个章节标题，每行一条，不要解释。",
            },
            {"role": "user", "content": query},
        ],
        "",
    )
    sections = parse_outline_from_text(outline_text, max_sections=max_sections)
    if not sections:
        sections = build_default_outline(query, max_sections=min(5, max_sections))
    plan = LongformPlan(
        query=query,
        title=query[:60] or "长文任务",
        sections=sections,
        max_sections=max_sections,
        max_chars_total=max_chars_total,
    )
    emit(
        "longform_outline",
        {
            "title": plan.title,
            "sections": [{"index": s.index, "title": s.title} for s in plan.sections],
        },
    )

    # 2) 分节填充
    parts: list[str] = [f"# {plan.title}\n"]
    chars = sum(len(p) for p in parts)
    done = 0
    skipped = 0
    for sec in plan.sections:
        if _stopped():
            sec.status = "skipped"
            skipped += 1
            emit("longform_section_skipped", {"index": sec.index, "reason": "cancelled"})
            break
        if (time.perf_counter() - t0) * 1000 > max_wall_ms:
            sec.status = "skipped"
            skipped += 1
            emit("longform_section_skipped", {"index": sec.index, "reason": "wall_timeout"})
            break
        if chars >= max_chars_total:
            sec.status = "skipped"
            skipped += 1
            emit("longform_section_skipped", {"index": sec.index, "reason": "budget_exhausted"})
            continue

        sec.status = "writing"
        emit("longform_section_start", {"index": sec.index, "title": sec.title})
        fallback = (
            f"## {sec.title}\n\n"
            f"（本节围绕「{sec.title}」展开：结合「{query}」说明背景、要点与注意项。"
            + (f"参考上下文：{source_context[:200]}" if source_context else "")
            + "）\n"
        )
        body = _chat(
            [
                {"role": "system", "content": "你是长文分节撰写助手，只输出本节正文。"},
                {"role": "user", "content": _section_prompt(plan, sec)},
            ],
            fallback,
        )
        sec.draft = body
        sec.status = "done"
        done += 1
        chunk = f"\n## {sec.title}\n\n{body.strip()}\n"
        parts.append(chunk)
        chars += len(chunk)
        emit(
            "longform_section_done",
            {"index": sec.index, "title": sec.title, "chars": len(chunk)},
        )

    final = "".join(parts).strip() + "\n"
    status = "succeeded"
    warning = None
    if skipped:
        status = "budget_exhausted" if not _stopped() else "cancelled"
        warning = f"有 {skipped} 节未完成，可继续写补齐"
    plan.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    result = LongformResult(
        plan=plan,
        final_text=final,
        status=status,
        sections_done=done,
        sections_skipped=skipped,
        warning=warning,
    )
    emit(
        "longform_done",
        {
            "status": status,
            "sections_done": done,
            "sections_skipped": skipped,
            "chars": len(final),
            "elapsed_ms": plan.elapsed_ms,
        },
    )
    return result


def iter_longform_events(
    query: str,
    **kwargs: Any,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """生成器封装：把 run_longform 的 on_event 转为 (name, payload) 流。"""
    box: list[tuple[str, dict[str, Any]]] = []

    def _collect(name: str, payload: dict[str, Any]) -> None:
        box.append((name, payload))
        # 立即 yield 不可行（同步回调）；调用方用 run_longform + on_event 更直接

    result = run_longform(query, on_event=_collect, **kwargs)
    for item in box:
        yield item
    yield ("longform_result", {
        "status": result.status,
        "text_len": len(result.final_text),
        "sections_done": result.sections_done,
    })
