"""Agent module for intelligent query routing and tool selection.

This module provides LLM-driven planning that replaces the regex-based
intent classifier. The LLM analyzes queries and decides which tools
to use, making the system behave like a real AI agent.
"""

from doc2mind.core.agent.planner import (
    AgentPlan,
    ToolPlan,
    plan_with_llm,
    TOOLS,
)

__all__ = [
    "AgentPlan",
    "ToolPlan",
    "plan_with_llm",
    "TOOLS",
]
