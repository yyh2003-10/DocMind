"""对话提示词双轨策略（P0）。

RAG 轨：保持现有「精准回答 + 禁 tool JSON + 篇幅原则」。
Delivery 轨：创作/科研/交付/续写任务，覆盖篇幅压制，允许充分展开。

只借鉴开源 agent 的能力分层概念，提示词与命名均为 DocMind 自研。
"""

from __future__ import annotations

from typing import Any

PROMPT_TRACK_RAG = "rag"
PROMPT_TRACK_DELIVERY = "delivery"

# 交付型意图：默认走 delivery 轨
DELIVERY_QUERY_TYPES = frozenset({"creative", "research"})

DEFAULT_CONTINUE_INSTRUCTION = (
    "请接着会话中上一条可能被截断的回答继续写。"
    "只输出续写部分，不要重复已经写过的内容；保持同一结构、口径与引用规则，"
    "直到本任务内容完整收束。"
)

CONTINUE_TRIGGER_WORDS = frozenset({"", "继续", "继续写", "接着写", "续写", "continue"})

# 交付模式覆盖段：追加在既有 system prompt 之后，避免大改共享提示词
DELIVERY_OVERRIDE = """【覆盖·交付/长文模式】
本回合按交付任务处理，篇幅以任务要求为准：
1. 不适用「简单事实题一段话 / 中等题 300-500 字」的压缩规则；
2. 报告/方案/课件/综述等应按结构完整展开，可用标题、分节、表格；
3. 禁止为凑字数空洞重复；资料不足时明确写出缺口与推测边界；
4. 引用编号仍只能来自本轮给出的资料列表，禁止编造；
5. 【P0 约束】正文中仍禁止输出 JSON 工具调用或 function_call；
   若检索不足，请用文字说明局限，不要罗列搜索词或伪工具 JSON。
"""

# 交付轨默认再抬升一档的输出预算下限（tokens）
DELIVERY_MIN_OUTPUT_TOKENS = 16384
DELIVERY_TOKEN_MULTIPLIER = 2


def resolve_prompt_track(
    *,
    query_type: str | None = None,
    creative_mode: str | None = None,
    explicit: str | None = None,
    continue_writing: bool = False,
) -> str:
    """解析本轮提示词轨。

    优先级：continue_writing > 显式 response_mode > 规划意图。
    """
    if continue_writing:
        return PROMPT_TRACK_DELIVERY
    exp = (explicit or "").strip().lower()
    if exp in (PROMPT_TRACK_RAG, PROMPT_TRACK_DELIVERY):
        return exp
    if exp in ("auto", ""):
        pass
    else:
        # 未知显式值：回落到自动
        pass
    qt = (query_type or "").strip().lower()
    if qt in DELIVERY_QUERY_TYPES:
        return PROMPT_TRACK_DELIVERY
    if creative_mode and str(creative_mode).strip():
        return PROMPT_TRACK_DELIVERY
    return PROMPT_TRACK_RAG


def apply_prompt_track(system_prompt: str, track: str) -> str:
    """把轨策略应用到 system prompt。RAG 原样返回；delivery 追加覆盖段。"""
    if track != PROMPT_TRACK_DELIVERY:
        return system_prompt or ""
    base = system_prompt or ""
    if "【覆盖·交付/长文模式】" in base:
        return base
    return (base.rstrip() + "\n\n" + DELIVERY_OVERRIDE).strip() + "\n"


def boost_max_tokens(
    effective: int | None,
    track: str,
    *,
    ceiling: int | None = None,
) -> int | None:
    """交付轨抬升输出预算；RAG 轨原样返回。

    effective 为 None（交由服务端默认）时不强行改写。
    """
    if track != PROMPT_TRACK_DELIVERY or not effective or effective < 1:
        return effective
    boosted = max(int(effective) * DELIVERY_TOKEN_MULTIPLIER, DELIVERY_MIN_OUTPUT_TOKENS)
    cap = ceiling if isinstance(ceiling, int) and ceiling >= 1 else 65536
    return min(boosted, cap)


def is_continue_query(query: str | None) -> bool:
    q = (query or "").strip().lower()
    return q in CONTINUE_TRIGGER_WORDS


def build_continue_query(user_query: str | None) -> str:
    """组装续写用户指令。空/「继续写」用默认指令；其它视为补充要求。"""
    q = (user_query or "").strip()
    if is_continue_query(q):
        return DEFAULT_CONTINUE_INSTRUCTION
    return f"{DEFAULT_CONTINUE_INSTRUCTION}\n\n补充要求：{q}"


def done_frame_extras(
    *,
    track: str,
    truncated: bool,
    continue_writing: bool = False,
) -> dict[str, Any]:
    """done 帧扩展字段（向后兼容：旧客户端可忽略未知键）。"""
    extras: dict[str, Any] = {
        "prompt_track": track,
        "truncated": bool(truncated),
        "continue_supported": True,
        "response_mode": "continue" if continue_writing else "normal",
    }
    if truncated:
        extras["continue_hint"] = (
            "回答可能因输出上限被截断，可点击「继续写」补全，或调大设置中的输出上限"
        )
    return extras
