"""Agent module for intelligent query routing and tool selection.

This module provides LLM-driven planning that replaces the regex-based
intent classifier. The LLM analyzes queries and decides which tools
to use, making the system behave like a real AI agent.
"""

from doc2mind.core.agent.planner import (
    AgentPlan,
    ToolPlan,
    plan_with_llm,
    user_facing_plan_reason,
    TOOLS,
)
from doc2mind.core.agent.prompt_policy import (
    PROMPT_TRACK_DELIVERY,
    PROMPT_TRACK_RAG,
    apply_prompt_track,
    boost_max_tokens,
    build_continue_query,
    done_frame_extras,
    resolve_prompt_track,
)

__all__ = [
    "AgentPlan",
    "ToolPlan",
    "plan_with_llm",
    "user_facing_plan_reason",
    "TOOLS",
    "PROMPT_TRACK_DELIVERY",
    "PROMPT_TRACK_RAG",
    "apply_prompt_track",
    "boost_max_tokens",
    "build_continue_query",
    "done_frame_extras",
    "resolve_prompt_track",
]
