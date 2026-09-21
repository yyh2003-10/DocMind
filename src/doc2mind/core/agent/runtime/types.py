"""Agent Runtime 公共类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"


@dataclass
class ToolCall:
    call_id: str
    tool_id: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    call_id: str
    tool_id: str
    status: ToolStatus
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
