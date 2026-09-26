"""对话回答模式路由：rag | agent | auto。

优先级：续写 > 消息级 chatMode（旧字段 agentMode/mode=agent ≡ agent）>
全局默认 chat_mode_default > 后端门禁 agent_mode_enabled。

产品约定（2026-09 确认）：
- 出厂默认模式 = rag（分发保守）
- auto 为规则版，偏保守：明确信号才升 Agent
- 总闸与默认模式分离：总闸关时 agent/auto→agent 可见降级 RAG
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MODE_RAG = "rag"
MODE_AGENT = "agent"
MODE_AUTO = "auto"

_DEF_RE = re.compile(r"是什么|什么是|啥是|定义|含义|概念|原理|指的是")
_AGENT_HINT_RE = re.compile(
    r"对比|选型|架构|故障|报错|排查|排错|优化方案|调研|综述|复现|根因|"
    r"怎么做|如何实现|设计一|评估|benchmark|压测"
)
_GREETING_RE = re.compile(r"^(你好|您好|hi|hello|嗨|在吗|谢谢|thanks)\b", re.I)


@dataclass(frozen=True)
class ChatModeDecision:
    """路由结果：真正执行的模式 + 用户可见原因。"""

    mode: str  # rag | agent
    requested: str  # rag | agent | auto
    reason: str
    degraded: bool = False  # 因门禁从 agent/auto 回落 rag


def normalize_mode(raw: str | None, fallback: str = MODE_RAG) -> str:
    v = (raw or "").strip().lower()
    if v in (MODE_RAG, MODE_AGENT, MODE_AUTO):
        return v
    if v in ("mode_agent", "agent_mode"):
        return MODE_AGENT
    return fallback


def _explicit_request(
    requested: str | None,
    agent_flag: bool,
    legacy_mode: str | None,
) -> str | None:
    """消息级显式模式；chatMode 优先于旧 agentMode/mode 字段。"""
    if requested is not None and str(requested).strip():
        r = str(requested).strip().lower()
        if r in (MODE_RAG, MODE_AGENT, MODE_AUTO):
            return r
    if agent_flag or normalize_mode(legacy_mode, "") == MODE_AGENT:
        return MODE_AGENT
    return None


def resolve_chat_mode(
    requested: str | None = None,
    *,
    agent_flag: bool = False,
    legacy_mode: str | None = None,
    agent_allowed: bool = False,
    default_mode: str = MODE_RAG,
    query: str = "",
    enable_web_search: bool = False,
    web_search_mode: str = "normal",
    continue_writing: bool = False,
    auto_enabled: bool = True,
) -> ChatModeDecision:
    """解析本轮应走 RAG 还是 Agent 工具循环。"""
    if continue_writing:
        return ChatModeDecision(MODE_RAG, MODE_RAG, "续写优先走 RAG，不进入 Agent 工具链")

    explicit = _explicit_request(requested, agent_flag, legacy_mode)
    req = explicit if explicit is not None else normalize_mode(default_mode, MODE_RAG)

    if req == MODE_RAG:
        return ChatModeDecision(MODE_RAG, req, "用户指定 RAG 模式")

    if req == MODE_AGENT:
        if agent_allowed:
            return ChatModeDecision(MODE_AGENT, req, "用户指定 Agent 工具循环")
        return ChatModeDecision(
            MODE_RAG,
            req,
            "后端未开启 Agent（agent_mode_enabled=false），本轮回落 RAG",
            degraded=True,
        )

    # auto
    if not auto_enabled:
        return ChatModeDecision(
            MODE_RAG,
            MODE_AUTO,
            "自动路由已关闭，使用 RAG",
        )
    if not agent_allowed:
        return ChatModeDecision(
            MODE_RAG,
            MODE_AUTO,
            "自动模式：后端未开启 Agent，使用 RAG",
            degraded=False,
        )

    web_mode = (web_search_mode or "normal").strip().lower()
    q = (query or "").strip()
    if not q:
        return ChatModeDecision(MODE_RAG, MODE_AUTO, "自动模式：空查询走 RAG")

    if enable_web_search and web_mode == "deep" and _looks_like_agent_work(q):
        return ChatModeDecision(
            MODE_AGENT, MODE_AUTO, "自动模式：深度联网 + 研究/定义型问题 → Agent 工具循环"
        )

    if enable_web_search and _DEF_RE.search(q) and not _GREETING_RE.search(q):
        return ChatModeDecision(
            MODE_AGENT, MODE_AUTO, "自动模式：定义/原理题 + 联网 → Agent 多源比对"
        )

    if _AGENT_HINT_RE.search(q) and len(q) >= 8:
        return ChatModeDecision(
            MODE_AGENT, MODE_AUTO, "自动模式：任务型/排错/对比类问题 → Agent 工具循环"
        )

    return ChatModeDecision(
        MODE_RAG, MODE_AUTO, "自动模式：常规问答/库内优先 → RAG（更快更省）"
    )


def _looks_like_agent_work(query: str) -> bool:
    q = (query or "").strip()
    if not q or len(q) < 4:
        return False
    if _GREETING_RE.search(q):
        return False
    return bool(_DEF_RE.search(q) or _AGENT_HINT_RE.search(q))


def decision_status_message(d: ChatModeDecision) -> str:
    """SSE status 帧文案（用户可见路由原因）。"""
    label = "Agent 工具循环" if d.mode == MODE_AGENT else "RAG 知识库问答"
    prefix = "⚠ " if d.degraded else "✔ "
    return f"{prefix}回答模式：{label} · {d.reason}"
