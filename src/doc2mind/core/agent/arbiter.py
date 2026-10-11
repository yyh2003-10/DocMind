"""创作 / 科研 / 问答模糊仲裁器（M1）。

三级仲裁架构（design 4.A.3 决策 A-3）：
- L0 规则快路径：确定性信号直通，零 LLM 成本
- L1 规则打分：creative/research/question/summary 四类计分，Δ 阈值仲裁
- L2 LLM 二判：L1 双高悬空或 intent_conflict_arbitration="llm" 时触发，带 2 秒软超时

仲裁器永远不单干：它是「LLM 规划帧」与「正则回退帧」之间的裁决与修正层。
LLM 可用时仅对明显冲突介入复核；LLM 不可用时替代规划器直接产出路由结论。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .intent import (
    _CREATIVE_MODE_MAP,
    _RESEARCH_LITERATURE_CTX,
    _RESEARCH_TASK_WORDS,
    map_creative_mode,
    map_research_task,
    _looks_like_research,
)

if TYPE_CHECKING:
    from doc2mind.core.agent.planner import AgentPlan
    from doc2mind.core.llm.base import LLMClient
    from doc2mind.core.config import Settings

logger = logging.getLogger(__name__)

# L1 打分阈值（design 4.A.3 伪代码，初值固化于此；跑分后微调见 config.py 注释）
_ARBITRATION_DELTA_THRESHOLD = 0.5  # Δ > 阈值 → 高者胜；Δ ≤ 阈值 → 进 L2
_ARBITRATION_L2_TIMEOUT = 2.0  # L2 LLM 二判软超时（秒）

# 闲聊 / 泛问兜底正则（design 决策 A-4）
_CHITCHAT_PATTERNS = re.compile(
    r"(随便聊聊|今天天气|给我讲个故事|闲聊|你好吗|在吗|你是谁|你是谁呀)",
    re.IGNORECASE,
)

# 纯问候正则（不含闲聊词：闲聊走 question 而非 greeting，design A-4）
_GREETING_ONLY_PATTERNS = re.compile(
    r'^(你好|您好|hi|hello|hey|嗨|早上好|下午好|晚上好|早安|晚安|'
    r'谢谢|感谢|thanks|thank|ok|好的|收到|了解|明白|'
    r'再见|拜拜|bye|goodbye)[\s!！.。,，?？~～]*$',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ArbitrationResult:
    """仲裁结果契约（design 8.2.1）。

    Attributes:
        winning: 胜出意图类别（"creative" | "research" | "question" | "summary" | "greeting"）
        sub_type: 子类型（creative→persona 模式；research→review/compare/draft）
        confidence: 置信度 0-1
        reason: 用户可见的中文仲裁依据（进 thinking 帧，不透出英文 meta）
        decider: 仲裁路径标记（"l0_regex" | "l1_score" | "l2_llm" | "llm_plan" | "regex_fallback"）
    """

    winning: str
    sub_type: str | None
    confidence: float
    reason: str
    decider: str


# ── L0 规则快路径 ───────────────────────────────────────────────────────────

def _l0_fast_path(query: str, history: list[dict[str, str]] | None) -> ArbitrationResult | None:
    """L0 确定性信号直通：问候 / 明确创作 / 明确科研。

    Returns None 表示 L0 未命中，需进入 L1 打分。
    """
    q = query.strip()
    if not q:
        return None

    # 问候 → greeting（仅纯问候；闲聊词走 question，design A-4）
    if _GREETING_ONLY_PATTERNS.match(q):
        return ArbitrationResult(
            winning="greeting",
            sub_type=None,
            confidence=1.0,
            reason="识别为问候/闲聊",
            decider="l0_regex",
        )

    # 强交付物创作（明确结构化交付物产物名词）优先于科研：
    # 「做成一份对比表格」「做个PPT」→ creative/table|ppt（即使含文献语境）
    creative_mode = map_creative_mode(q)
    explicit_artifact = ("ppt", "幻灯片", "演示", "课件", "教案", "表格", "矩阵",
                         "甘特", "看板", "网页", "html", "excel", "xlsx")
    if creative_mode and any(a in q for a in explicit_artifact):
        mode_label = {"ppt": "PPT", "doc": "研报/文档", "table": "对比表/矩阵",
                      "lesson": "课件/教案", "web": "看板/网页"}.get(creative_mode, creative_mode)
        return ArbitrationResult(
            winning="creative",
            sub_type=creative_mode,
            confidence=0.95,
            reason=f"识别为创作任务（{mode_label}）",
            decider="l0_regex",
        )

    # 明确科研：文献语境 + 分析任务 / 独立短语 / 多轮弱命中（design 4.A.2）
    if _looks_like_research(q, history):
        sub = map_research_task(q) or "review"
        sub_label = {"review": "综述/大纲", "compare": "观点对比", "draft": "带引用草稿"}.get(sub, sub)
        return ArbitrationResult(
            winning="research",
            sub_type=sub,
            confidence=0.95,
            reason=f"识别为科研写作（{sub_label}）",
            decider="l0_regex",
        )

    # 弱交付物创作：创作动词 + 产物名词（写研报 / 做课件）
    # 排除「总结 / 对比 / 分析」纯分析词（无产物名词）
    if creative_mode:
        artifact_nouns = ("研报", "报告", "公文", "文档", "对比表",
                          "课件", "教案", "看板", "网页", "html")
        has_artifact = any(a in q for a in artifact_nouns)
        if has_artifact or creative_mode in ("ppt", "doc", "web", "lesson"):
            mode_label = {"ppt": "PPT", "doc": "研报/文档", "table": "对比表/矩阵",
                          "lesson": "课件/教案", "web": "看板/网页"}.get(creative_mode, creative_mode)
            return ArbitrationResult(
                winning="creative",
                sub_type=creative_mode,
                confidence=0.95,
                reason=f"识别为创作任务（{mode_label}）",
                decider="l0_regex",
            )

    return None


# ── L1 规则打分 ─────────────────────────────────────────────────────────────

def _score_signals(query: str, history: list[dict[str, str]] | None) -> tuple[float, float, float, float]:
    """对 creative / research / question / summary 四类信号打分（design 4.A.3 伪代码）。

    Returns:
        (c, r, q, s) 四类得分
    """
    q = query.strip()
    c = r = qs = s = 0.0  # creative, research, question, summary

    # creative 信号：明确创作产物名词 + 创作动词
    creative_mode = map_creative_mode(q)
    if creative_mode:
        c += 2.0
    artifact_nouns = ("对比表", "矩阵", "甘特", "PPT", "研报", "课件", "看板", "文档", "报告")
    if any(a in q for a in artifact_nouns):
        c += 1.0
    if "对比" in q and ("表" in q or "矩阵" in q or "甘特" in q):
        c += 1.0  # 「对比表」→ creative/table

    # research 信号：文献语境 + 分析/写作任务
    has_lit = any(w in q for w in _RESEARCH_LITERATURE_CTX)
    has_task = any(w in q for w in _RESEARCH_TASK_WORDS)
    if has_lit:
        if has_task:
            r += 3.0
        else:
            r += 1.0
    elif has_task:
        # 弱命中：仅任务词，历史上文含文献语境
        if history:
            recent = history[-4:]
            if any(
                any(lit in (m.get("content") or "") for lit in _RESEARCH_LITERATURE_CTX)
                for m in recent if m.get("role") == "user"
            ):
                r += 2.0
            else:
                r += 1.0

    # question 信号：疑问句式 / 对比但无产物与文献
    question_markers = ("什么", "谁", "哪个", "多少", "哪里", "是否", "能不能", "如何", "为什么", "怎么")
    if any(m in q for m in question_markers) and not (c or r):
        qs += 1.0
    if "对比" in q and not any(a in q for a in artifact_nouns) and not has_lit:
        qs += 1.0  # 「对比 A 和 B 的区别」→ question

    # summary 信号（与 intent._SUMMARY_PATTERNS 语义对齐，防关键词漂移）
    summary_markers = ("总结", "概括", "归纳", "提炼", "梳理", "概述", "摘要", "要点")
    if any(m in q for m in summary_markers):
        s += 1.0

    return c, r, qs, s


def _l1_arbitrate(c: float, r: float, qs: float, s: float) -> ArbitrationResult | None:
    """L1 规则打分仲裁：有胜者返回 ArbitrationResult，悬空返回 None 触发 L2。"""
    # 单类高胜
    if c >= 2 and r == 0 and qs == 0:
        return ArbitrationResult(
            winning="creative",
            sub_type=map_creative_mode("") or "ppt",
            confidence=min(0.9, c / 3),
            reason="创作信号明确",
            decider="l1_score",
        )
    if r >= 2 and c == 0 and qs == 0:
        return ArbitrationResult(
            winning="research",
            sub_type=map_research_task("") or "review",
            confidence=min(0.9, r / 3),
            reason="科研写作信号明确",
            decider="l1_score",
        )
    if qs >= 1 and c == 0 and r == 0:
        return ArbitrationResult(
            winning="question",
            sub_type=None,
            confidence=min(0.8, qs / 2),
            reason="识别为知识问答",
            decider="l1_score",
        )
    if s >= 1 and c == 0 and r == 0 and qs == 0:
        return ArbitrationResult(
            winning="summary",
            sub_type=None,
            confidence=min(0.8, s / 2),
            reason="识别为内容总结",
            decider="l1_score",
        )

    # 冲突：creative ⇄ research（任一 ≥2，另一 ≥1 即进入仲裁）
    if c >= 2 and r >= 1:
        delta = abs(c - r)
        if delta > _ARBITRATION_DELTA_THRESHOLD:
            winner = "creative" if c > r else "research"
            sub = map_creative_mode("") if winner == "creative" else map_research_task("") or "review"
            return ArbitrationResult(
                winning=winner,
                sub_type=sub,
                confidence=0.7 + delta * 0.1,
                reason=f"规则仲裁：{c:.1f} vs {r:.1f}，{delta:.1f} 差距判定为{'创作' if winner == 'creative' else '科研写作'}",
                decider="l1_score",
            )
        # Δ ≤ 阈值 → 悬空，进 L2
        return None

    # 默认：沿用 LLM 初判 / regex fallback（不干预）
    return None


# ── L2 LLM 二判 ─────────────────────────────────────────────────────────────

def _l2_llm_arbitrate(
    query: str,
    llm_client: LLMClient | None,
    settings: Settings,
) -> ArbitrationResult | None:
    """L2 LLM 二判：仅当 L1 悬空且 LLM 可用时触发。

    2 秒软超时，失败静默回 L1 结论（悬空）并在 reason 标注。
    """
    if llm_client is None:
        return None

    prompt = f"""你是一个意图仲裁器。请从以下三个选项中选择最符合用户意图的一个，并给出一句中文理由。

用户问题：{query}

选项：
- creative：用户明确要求产出创作物（PPT/研报/课件/对比表/看板等文件）
- research：用户围绕文献/论文/资料做综述、观点对比、带引用写作
- question：普通知识问答、对比分析（无明确产物、无文献语境）

请严格按以下 JSON 格式输出（仅输出 JSON，不要其他内容）：
{{"winning": "creative|research|question", "reason": "一句中文理由"}}"""

    try:
        # 沿用 llm_timeout 配置，但 L2 硬上限 2 秒
        timeout = min(settings.llm_timeout or 30, _ARBITRATION_L2_TIMEOUT)
        if timeout <= 0:
            timeout = _ARBITRATION_L2_TIMEOUT

        response = llm_client.chat(
            messages=[{"role": "system", "content": "你是意图仲裁器，只输出 JSON。"},
                      {"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=150,
            timeout=timeout,
        )

        # 解析 JSON
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if not json_match:
            return None
        data = json.loads(json_match.group(0))
        winning = data.get("winning", "question")
        if winning not in ("creative", "research", "question"):
            winning = "question"
        reason = data.get("reason", "LLM 二判确认")

        return ArbitrationResult(
            winning=winning,
            sub_type=map_creative_mode(query) if winning == "creative" else (
                map_research_task(query) or "review" if winning == "research" else None
            ),
            confidence=0.85,
            reason=f"LLM 二判确认：{reason}",
            decider="l2_llm",
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("L2 LLM 仲裁失败，静默回退：%s", e)
        return None


# ── 主入口 ──────────────────────────────────────────────────────────────────

# 三角含糊区：creative⇄research⇄question（design 4.A.3 决策 A-3）
_ARBITRATION_TRIANGLE = frozenset({"creative", "research", "question"})


def arbitrate(
    query: str,
    history: list[dict[str, str]] | None,
    llm_plan: AgentPlan | None,
    llm_client: LLMClient | None,
    settings: Settings,
) -> ArbitrationResult:
    """三级仲裁主入口。

    仲裁器永远不单干：它是「LLM 规划帧」与「正则回退帧」之间的裁决与修正层。
    - 触发量严格压缩：仅 creative⇄research⇄question 三角含糊区进 L1/L2；
      其余路径（troubleshoot/summary/task 等）直接沿用 LLM 初判。
    - L0/L1 规则强判胜出时作为「明显冲突」拦截并纠正 LLM 初判；
      规则无法判定时沿用 LLM 初判（LLM 初判仍为主）。
    - LLM 不可用时替代规划器直接产出路由结论。
    - research 结果受 intent_research_enabled 门控：开关关闭时回落 question。

    Args:
        query: 用户查询文本
        history: 多轮对话历史
        llm_plan: LLM 规划器产出的 AgentPlan（可能为 None，即 LLM 不可用）
        llm_client: LLM 客户端（可能为 None）
        settings: 运行时配置

    Returns:
        ArbitrationResult 仲裁结果
    """
    research_enabled = getattr(settings, "intent_research_enabled", True)
    arbitration_mode = getattr(settings, "intent_conflict_arbitration", "rules")

    def _result_from_llm(plan: AgentPlan, reason: str) -> ArbitrationResult:
        return ArbitrationResult(
            winning=plan.query_type,
            sub_type=plan.creative_mode or plan.research_task,
            confidence=0.6,
            reason=reason,
            decider="llm_plan",
        )

    def _gate_research(result: ArbitrationResult) -> ArbitrationResult:
        """research 开关门控：关闭时 research 结论回落 question。"""
        if result.winning == "research" and not research_enabled:
            return ArbitrationResult(
                winning="question",
                sub_type=None,
                confidence=result.confidence,
                reason="科研写作路由未启用，按知识问答处理",
                decider=result.decider,
            )
        return result

    # 0. none 模式：关闭仲裁，完全沿用 LLM 初判（LLM 不可用时走规则例行判断）
    if arbitration_mode == "none":
        if llm_plan is not None:
            return _result_from_llm(llm_plan, "仲裁关闭，沿用 LLM 规划器初判")

    # 1. L0 快路径（确定性信号）
    l0_result = _l0_fast_path(query, history)
    if l0_result is not None:
        # LLM 初判与 L0 一致 → 沿用 LLM（LLM 初判仍为主）；冲突 → L0 修正
        if llm_plan is not None and llm_plan.query_type == l0_result.winning:
            return _result_from_llm(llm_plan, "规则与 LLM 初判一致，沿用 LLM 规划器初判")
        return _gate_research(l0_result)

    # 2. LLM 初判不在三角含糊区 → 直接沿用（不仲裁）
    if llm_plan is not None and llm_plan.query_type not in _ARBITRATION_TRIANGLE:
        return _result_from_llm(llm_plan, "非三角含糊区，沿用 LLM 规划器初判")

    # 3. L1 打分
    c, r, qs, s = _score_signals(query, history)
    l1_result = _l1_arbitrate(c, r, qs, s)

    # 4. LLM 规划帧存在（LLM 可用）：
    #    L1 有胜者且与 LLM 初判冲突 → 修正（明显冲突）；否则沿用 LLM
    if llm_plan is not None:
        if l1_result is not None and l1_result.winning != llm_plan.query_type:
            return _gate_research(l1_result)
        # L1 悬空或与 LLM 一致 → 三角区内沿用 LLM（或 L2 二判）
        if arbitration_mode == "llm":
            l2_result = _l2_llm_arbitrate(query, llm_client, settings)
            if l2_result is not None:
                return l2_result
        return _result_from_llm(llm_plan, "沿用 LLM 规划器初判")

    # 5. LLM 不可用（正则回退帧）：
    if l1_result is not None:
        return _gate_research(l1_result)
    # L1 悬空 → 保守判 question（宁可多检索，不误触创作 persona 切换）
    return ArbitrationResult(
        winning="question",
        sub_type=None,
        confidence=0.5,
        reason="规则与 LLM 均无法判定，保守选择知识问答",
        decider="regex_fallback",
    )