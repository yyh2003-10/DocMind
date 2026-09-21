"""权限分级与写入策略（P1 骨架）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Literal


class PermissionLevel(IntEnum):
    """L0 只读外部资料 → L3 危险操作。"""

    L0_READONLY = 0
    L1_WORKSPACE_READ = 1
    L2_WORKSPACE_WRITE = 2
    L3_FORBIDDEN = 3


WritePolicyName = Literal["ask", "session_allow", "always_allow_workspace"]


class PermissionDecision:
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PermissionGate:
    """权限门：只回答「允不允许」，不执行副作用。"""

    write_policy: WritePolicyName = "ask"
    session_write_allowed: bool = False

    def decide(self, level: PermissionLevel | int) -> str:
        lv = PermissionLevel(int(level))
        if lv == PermissionLevel.L3_FORBIDDEN:
            return PermissionDecision.DENY
        if lv <= PermissionLevel.L1_WORKSPACE_READ:
            return PermissionDecision.ALLOW
        # L2 workspace write
        if self.write_policy == "always_allow_workspace":
            return PermissionDecision.ALLOW
        # 会话内已授权：ask / session_allow 均放行后续写入
        if self.session_write_allowed:
            return PermissionDecision.ALLOW
        return PermissionDecision.ASK

    def grant_session_write(self) -> None:
        self.session_write_allowed = True
