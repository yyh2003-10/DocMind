"""交付物体检自迭代：低分内容按体检建议自动修订一轮（P2-4）。

约束：
- 默认最多 1 轮修订，避免失控烧 token；
- 无 LLM / 调用失败时返回原文，不阻断导出；
- 修订提示只要求改内容结构，不引入无依据事实。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 低于该分触发一次自动修订
DEFAULT_REVISE_SCORE = 75
# 最多自动修订轮数（产品约定 1–2）
DEFAULT_MAX_REVISE_ROUNDS = 1


def _format_issues(report: Any, limit: int = 8) -> str:
    issues = getattr(report, "issues", None) or []
    recs = getattr(report, "recommendations", None) or []
    lines: list[str] = []
    for it in issues[:limit]:
        level = getattr(getattr(it, "level", ""), "value", getattr(it, "level", ""))
        cat = getattr(it, "category", "")
        msg = getattr(it, "message", "")
        fix = getattr(it, "fix_suggestion", "")
        page = getattr(it, "slide_index", None)
        loc = f"（第 {page} 页）" if page is not None else ""
        lines.append(f"- [{level}] {cat}{loc}: {msg}" + (f" → {fix}" if fix else ""))
    for r in recs[:5]:
        lines.append(f"- 建议: {r}")
    return "\n".join(lines) if lines else "- 未列出具体问题，请按通用演示文稿规范补强结构与可读性。"


def build_revise_prompt(content: str, report: Any) -> list[dict[str, str]]:
    issues = _format_issues(report)
    score = getattr(report, "score", 0)
    system = (
        "你是演示文稿修订助手。只根据体检问题修改内容结构与表达，"
        "不要编造资料中没有的事实/数据/规范条文。"
        "保持原有 Artifact 语法（`---` 分页、`### 卡片标题`、`<!-- note: ... -->` 备注、`<!-- layout: ... -->`）。"
        "输出完整修订后的全文，不要解释过程。"
    )
    user = (
        f"当前体检得分 {score}/100。\n"
        f"体检问题：\n{issues}\n\n"
        f"请输出修订后的完整内容：\n\n{content}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def revise_artifact_content(
    content: str,
    report: Any,
    llm_client: Any = None,
    *,
    max_rounds: int = DEFAULT_MAX_REVISE_ROUNDS,
    score_threshold: int = DEFAULT_REVISE_SCORE,
) -> tuple[str, int, str | None]:
    """必要时修订内容。返回 (new_content, revise_rounds_used, note)。

    - score >= 阈值：原样返回
    - 无 LLM / 修订失败：原样返回并附 note
    - 成功：返回修订后内容与轮数（≤ max_rounds）
    """
    score = int(getattr(report, "score", 100) or 100)
    if score >= score_threshold:
        return content, 0, None
    if llm_client is None:
        return content, 0, f"体检 {score} 分偏低，但未配置 LLM，跳过自动修订"
    if max_rounds <= 0:
        return content, 0, None

    current = content
    used = 0
    note: str | None = None
    for _ in range(max_rounds):
        try:
            messages = build_revise_prompt(current, report)
            revised = llm_client.chat(messages, temperature=0.3, max_tokens=8192)
            revised = (revised or "").strip()
            if not revised or revised == current:
                note = "修订无变化，保留原稿"
                break
            current = revised
            used += 1
            note = f"已按体检建议自动修订 {used} 轮（原分 {score}）"
            # 只做 1 轮时不再 inspect 循环；多轮时由调用方决定是否再检
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("交付物自动修订失败: %s", exc)
            note = f"自动修订失败：{exc}"
            break
    return current, used, note
