"""工具注册表：模型只面对 schema，执行器由宿主注入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from doc2mind.core.agent.runtime.permissions import PermissionLevel
from doc2mind.core.agent.runtime.types import ToolCall, ToolResult, ToolStatus

Executor = Callable[[ToolCall], ToolResult]


@dataclass
class ToolSpec:
    tool_id: str
    display_name: str
    description: str
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    permission_level: PermissionLevel = PermissionLevel.L0_READONLY
    enabled_by_default: bool = True
    weak_model_allowed: bool = True
    result_budget_chars: int = 4000
    timeout_ms: int = 20_000

    def schema_for_model(self) -> dict[str, Any]:
        return {
            "name": self.tool_id,
            "description": self.description,
            "parameters": self.parameters_schema,
        }


def builtin_tool_specs() -> list[ToolSpec]:
    """首批工具 schema（执行器后续接线；此处只定义契约）。"""
    return [
        ToolSpec(
            tool_id="kb_search",
            display_name="知识库检索",
            description="从本地知识库检索与问题相关的文档片段与来源。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索词"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        ),
        ToolSpec(
            tool_id="web_search",
            display_name="联网搜索",
            description=(
                "检索公开网络资料，补充知识库未覆盖的最新信息。"
                "证据不足或来源单一时，可用不同关键词再次调用以交叉印证。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索词"},
                    "mode": {
                        "type": "string",
                        "enum": ["normal", "deep"],
                        "description": "normal=常规；deep=扩源比对",
                    },
                },
                "required": ["query"],
            },
        ),
        ToolSpec(
            tool_id="list_workspace",
            display_name="列出工作区文件",
            description="列出当前会话工作区中的文件。",
            parameters_schema={
                "type": "object",
                "properties": {"sub": {"type": "string", "description": "子目录，可空"}},
            },
            permission_level=PermissionLevel.L1_WORKSPACE_READ,
        ),
        ToolSpec(
            tool_id="read_workspace_file",
            display_name="读工作区文件",
            description="读取工作区内允许类型的文本文件。",
            parameters_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            permission_level=PermissionLevel.L1_WORKSPACE_READ,
        ),
        ToolSpec(
            tool_id="write_workspace_file",
            display_name="写工作区文件",
            description="将文本写入工作区（需写入权限）。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            permission_level=PermissionLevel.L2_WORKSPACE_WRITE,
        ),
        ToolSpec(
            tool_id="export_artifact",
            display_name="导出交付物",
            description="把结构化内容导出为 PPTX/DOCX/XLSX/HTML 文件。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "format": {"type": "string", "enum": ["pptx", "docx", "xlsx", "html"]},
                    "title": {"type": "string"},
                },
                "required": ["content", "format"],
            },
            permission_level=PermissionLevel.L2_WORKSPACE_WRITE,
            weak_model_allowed=False,
        ),
        ToolSpec(
            tool_id="inspect_artifact",
            display_name="交付物体检",
            description="对创作交付物进行质量诊断评分。",
            parameters_schema={
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
            permission_level=PermissionLevel.L1_WORKSPACE_READ,
        ),
    ]


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._executors: dict[str, Executor] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec, executor: Executor | None = None) -> None:
        self._specs[spec.tool_id] = spec
        if executor is not None:
            self._executors[spec.tool_id] = executor

    def bind_executor(self, tool_id: str, executor: Executor) -> None:
        if tool_id not in self._specs:
            raise KeyError(tool_id)
        self._executors[tool_id] = executor

    def get(self, tool_id: str) -> ToolSpec | None:
        return self._specs.get(tool_id)

    def list_specs(self, *, enabled_only: bool = True) -> list[ToolSpec]:
        specs = list(self._specs.values())
        if enabled_only:
            specs = [s for s in specs if s.enabled_by_default]
        return specs

    def model_schemas(self, *, weak_model: bool = False) -> list[dict[str, Any]]:
        out = []
        for spec in self.list_specs():
            if weak_model and not spec.weak_model_allowed:
                continue
            out.append(spec.schema_for_model())
        return out

    def execute(self, call: ToolCall) -> ToolResult:
        spec = self._specs.get(call.tool_id)
        if spec is None:
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status=ToolStatus.ERROR,
                error=f"unknown tool: {call.tool_id}",
            )
        executor = self._executors.get(call.tool_id)
        if executor is None:
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status=ToolStatus.ERROR,
                error=f"executor not bound: {call.tool_id}",
            )
        return executor(call)
