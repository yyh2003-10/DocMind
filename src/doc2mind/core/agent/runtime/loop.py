"""Agent Loop 控制器骨架（P1）。

P1 提供：
- 步数/工具次数/墙钟预算
- 取消标志
- 基于 Mock 模型的可测回路（无真实 LLM 也可验证状态机）

真实 provider tool-calling 接线属于后续里程碑；此处只定契约与状态机。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from doc2mind.core.agent.runtime.permissions import PermissionDecision, PermissionGate
from doc2mind.core.agent.runtime.registry import ToolRegistry
from doc2mind.core.agent.runtime.types import ToolCall, ToolResult, ToolStatus


class LoopStatus:
    IDLE = "idle"
    PLANNING = "planning"
    RUNNING_MODEL = "running_model"
    AWAITING_PERMISSION = "awaiting_permission"
    RUNNING_TOOL = "running_tool"
    COMPOSING_FINAL = "composing_final"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass
class LoopBudget:
    max_steps: int = 8
    max_tool_calls: int = 16
    max_wall_ms: int = 180_000
    max_same_tool_failures: int = 2


@dataclass
class AgentLoopResult:
    status: str
    final_text: str = ""
    steps: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    warning: str | None = None
    transcript: list[dict[str, Any]] = field(default_factory=list)


class ModelTurn(Protocol):
    """模型单轮输出契约：要么给最终正文，要么给 tool_calls。"""

    final_text: str
    tool_calls: list[ToolCall]


@dataclass
class MockModelTurn:
    final_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LoopController:
    def __init__(
        self,
        registry: ToolRegistry,
        gate: PermissionGate | None = None,
        budget: LoopBudget | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.registry = registry
        self.gate = gate or PermissionGate()
        self.budget = budget or LoopBudget()
        self.on_event = on_event
        self.status = LoopStatus.IDLE
        self._cancelled = False
        self._started_at: float | None = None

    def cancel(self) -> None:
        self._cancelled = True
        self._emit("cancelled", {})

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(event, payload)

    def _budget_ok(self, steps: int, tool_count: int) -> bool:
        if steps >= self.budget.max_steps:
            return False
        if tool_count >= self.budget.max_tool_calls:
            return False
        if self._started_at is not None:
            elapsed = (time.perf_counter() - self._started_at) * 1000
            if elapsed > self.budget.max_wall_ms:
                return False
        return True

    def run(
        self,
        model_fn: Callable[[list[dict[str, Any]], int], ModelTurn],
        seed_messages: list[dict[str, Any]] | None = None,
        *,
        auto_approve_session: bool = False,
    ) -> AgentLoopResult:
        """执行 loop。

        model_fn(messages, step) -> ModelTurn
        auto_approve_session: 测试用；为 True 时 L2 ask 自动视为会话已授权。
        """
        self._started_at = time.perf_counter()
        self._cancelled = False
        messages = list(seed_messages or [])
        steps = 0
        tool_count = 0
        calls: list[ToolCall] = []
        results: list[ToolResult] = []
        denied: list[str] = []
        transcript: list[dict[str, Any]] = []
        fail_streak: dict[str, int] = {}
        final_text = ""
        warning: str | None = None
        status = LoopStatus.RUNNING_MODEL

        if auto_approve_session:
            self.gate.grant_session_write()

        while True:
            if self._cancelled:
                status = LoopStatus.CANCELLED
                warning = "已取消"
                break
            if not self._budget_ok(steps, tool_count):
                status = LoopStatus.BUDGET_EXHAUSTED
                warning = "预算耗尽，输出当前最佳结果"
                break

            self.status = LoopStatus.RUNNING_MODEL
            turn = model_fn(messages, steps)
            steps += 1
            transcript.append(
                {
                    "step": steps,
                    "type": "model",
                    "final_text": turn.final_text,
                    "tool_calls": [
                        {"tool_id": c.tool_id, "arguments": c.arguments}
                        for c in turn.tool_calls
                    ],
                }
            )

            if not turn.tool_calls:
                final_text = turn.final_text
                status = LoopStatus.SUCCEEDED
                break

            for call in turn.tool_calls:
                if self._cancelled:
                    status = LoopStatus.CANCELLED
                    warning = "已取消"
                    break
                if tool_count >= self.budget.max_tool_calls:
                    status = LoopStatus.BUDGET_EXHAUSTED
                    warning = "工具调用次数已达上限"
                    break

                spec = self.registry.get(call.tool_id)
                level = spec.permission_level if spec else 0
                decision = self.gate.decide(level)
                if decision == PermissionDecision.DENY:
                    denied.append(call.tool_id)
                    result = ToolResult(
                        call_id=call.call_id,
                        tool_id=call.tool_id,
                        status=ToolStatus.DENIED,
                        error="permission denied",
                    )
                elif decision == PermissionDecision.ASK:
                    # P1 骨架：无 UI 挂起通道时，记 denied 并让模型改道
                    denied.append(call.tool_id)
                    result = ToolResult(
                        call_id=call.call_id,
                        tool_id=call.tool_id,
                        status=ToolStatus.DENIED,
                        error="permission ask not granted",
                    )
                    self.status = LoopStatus.AWAITING_PERMISSION
                    self._emit(
                        "permission_request",
                        {"call_id": call.call_id, "tool_id": call.tool_id},
                    )
                else:
                    self.status = LoopStatus.RUNNING_TOOL
                    self._emit(
                        "tool_call",
                        {"call_id": call.call_id, "tool_id": call.tool_id},
                    )
                    result = self.registry.execute(call)

                calls.append(call)
                results.append(result)
                tool_count += 1
                transcript.append(
                    {
                        "step": steps,
                        "type": "tool_result",
                        "call_id": result.call_id,
                        "tool_id": result.tool_id,
                        "status": result.status.value,
                        "summary": result.summary or result.error or "",
                    }
                )
                self._emit(
                    "tool_result",
                    {
                        "call_id": result.call_id,
                        "tool_id": result.tool_id,
                        "status": result.status.value,
                    },
                )
                if result.status == ToolStatus.ERROR:
                    fail_streak[call.tool_id] = fail_streak.get(call.tool_id, 0) + 1
                    if fail_streak[call.tool_id] >= self.budget.max_same_tool_failures:
                        warning = f"工具 {call.tool_id} 连续失败，将收尾"
                else:
                    fail_streak[call.tool_id] = 0

                messages = messages + [
                    {
                        "role": "tool",
                        "tool_call_id": result.call_id,
                        "name": result.tool_id,
                        "content": result.summary
                        or result.error
                        or result.status.value,
                    }
                ]

            if status in (LoopStatus.CANCELLED, LoopStatus.BUDGET_EXHAUSTED):
                # 预算耗尽时若尚无 final_text，尽量再要一次模型收尾（不执行新工具）
                if status == LoopStatus.BUDGET_EXHAUSTED and not final_text and not self._cancelled:
                    try:
                        self.status = LoopStatus.COMPOSING_FINAL
                        final_turn = model_fn(messages, steps)
                        steps += 1
                        final_text = final_turn.final_text
                    except Exception:  # noqa: BLE001
                        pass
                break
            if warning and "连续失败" in warning:
                # 进入收尾：再给模型一次基于轨迹作答的机会
                self.status = LoopStatus.COMPOSING_FINAL
                final_turn = model_fn(messages, steps)
                steps += 1
                final_text = final_turn.final_text
                status = LoopStatus.BUDGET_EXHAUSTED
                break

        self.status = status
        return AgentLoopResult(
            status=status,
            final_text=final_text,
            steps=steps,
            tool_calls=calls,
            tool_results=results,
            denied=denied,
            warning=warning,
            transcript=transcript,
        )


def run_mock_loop(
    turns: list[MockModelTurn],
    registry: ToolRegistry,
    gate: PermissionGate | None = None,
    budget: LoopBudget | None = None,
    *,
    auto_approve_session: bool = True,
) -> AgentLoopResult:
    """测试辅助：按预置 turn 序列驱动 loop。"""
    controller = LoopController(registry, gate=gate, budget=budget)
    idx = {"i": 0}

    def model_fn(_messages: list[dict[str, Any]], _step: int) -> MockModelTurn:
        i = idx["i"]
        idx["i"] = i + 1
        if i < len(turns):
            return turns[i]
        return MockModelTurn(final_text="(mock model exhausted)")

    return controller.run(
        model_fn,
        seed_messages=[{"role": "user", "content": "mock task"}],
        auto_approve_session=auto_approve_session,
    )
