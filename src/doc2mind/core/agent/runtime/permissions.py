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


class PermissionBroker:
    """L2 权限挂起通道：loop 发出 permission_request 后阻塞等待 UI/HTTP 裁决。

    无 UI 超时则视为 deny，让模型改道；有 UI 时 POST /v1/agent/permission/{id} 解除挂起。
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._pending: dict[str, list] = {}  # request_id -> [Event, result_str]

    def begin(self, request_id: str) -> None:
        """先登记 pending，再 emit 给 UI，避免 resolve 早于登记而丢失。"""
        import threading

        with self._lock:
            if request_id not in self._pending:
                self._pending[request_id] = [threading.Event(), "timeout"]

    def request(self, request_id: str, timeout: float = 60.0) -> str:
        """阻塞等待裁决；返回 allow | deny | timeout。"""
        import threading

        self.begin(request_id)
        with self._lock:
            item = self._pending.get(request_id)
        if item is None:
            return "timeout"
        ev = item[0]
        ev.wait(timeout)
        with self._lock:
            item = self._pending.pop(request_id, None)
        if item is None:
            return "timeout"
        return str(item[1])

    def resolve(self, request_id: str, decision: str) -> bool:
        """由 HTTP/UI 调用：allow / deny。"""
        d = decision.strip().lower()
        if d not in ("allow", "deny"):
            d = "deny"
        with self._lock:
            item = self._pending.get(request_id)
            if item is None:
                return False
            item[1] = d
            item[0].set()
            return True

    def pending_ids(self) -> list[str]:
        with self._lock:
            return list(self._pending.keys())


# 进程内共享 broker（单后端单进程；Agent 流与 HTTP 裁决共用）
GLOBAL_PERMISSION_BROKER = PermissionBroker()