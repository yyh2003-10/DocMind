"""研究任务上下文：把任务规格与用户问题、检索 query 分离。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_MAX_FIELD_LENGTH = 500


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return text[:_MAX_FIELD_LENGTH]


@dataclass(frozen=True)
class TaskContext:
    """一轮研究任务的轻量规格，不承担会话状态持久化。"""

    goal: str = ""
    audience: str = ""
    deliverable: str = ""
    citation_style: str = ""
    constraints: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "TaskContext":
        if not value:
            return cls()
        return cls(
            goal=_clean(value.get("goal")),
            audience=_clean(value.get("audience")),
            deliverable=_clean(value.get("deliverable")),
            citation_style=_clean(value.get("citation_style", value.get("citationStyle"))),
            constraints=_clean(value.get("constraints")),
        )

    @property
    def is_empty(self) -> bool:
        return not any((self.goal, self.audience, self.deliverable, self.citation_style, self.constraints))

    def to_prompt_block(self) -> str:
        """渲染为用户可控的任务规格；不应拼入检索 query 或来源列表。"""
        if self.is_empty:
            return ""
        rows = [
            ("目标", self.goal),
            ("受众", self.audience),
            ("交付物", self.deliverable),
            ("引用规范", self.citation_style),
            ("约束", self.constraints),
        ]
        lines = ["【本轮任务规格】", "以下字段是用户提供的任务要求，不是知识库证据；不要将其当作事实来源。"]
        lines.extend(f"- {label}：{value}" for label, value in rows if value)
        return "\n".join(lines)


def task_context_prompt(value: Mapping[str, Any] | TaskContext | None) -> str:
    """兼容调用方传入字典或已构造的 TaskContext。"""
    if isinstance(value, TaskContext):
        return value.to_prompt_block()
    return TaskContext.from_mapping(value).to_prompt_block()

