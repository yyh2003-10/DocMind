"""LLM-driven agent planner.

Replaces the regex-based intent classifier with an LLM-powered planning
step. The LLM analyzes the user's query and decides:
1. What type of query this is (greeting, question, task, etc.)
2. Which tools to activate and in what order
3. What search queries to use for each tool
4. Whether the answer needs multiple reasoning steps

This makes the system behave like a real AI agent rather than a
rule-based pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .intent import map_creative_mode

if TYPE_CHECKING:
    from doc2mind.core.llm.base import LLMClient

logger = logging.getLogger(__name__)

# 创作模式集合（persona 取值），与 _PERSONA_PROMPTS / CREATIVE_PERSONA_PROMPTS 对应
CREATIVE_MODES = ("ppt", "doc", "lesson", "table", "web")


# ── Available Tools ─────────────────────────────────────────────────────────
TOOLS = {
    "knowledge_base": {
        "name": "本地知识库检索",
        "description": "从用户本地的知识库中检索相关文档和信息。适用于：查找已摄入的文档内容、获取项目特定知识、查找内部规范和流程。",
        "best_for": ["查找文档", "获取项目知识", "查找规范", "查找历史记录"],
    },
    "web_search": {
        "name": "联网搜索",
        "description": "通过互联网搜索最新信息。适用于：查找最新技术动态、获取官方文档、查找解决方案、对比不同方案。",
        "best_for": ["最新信息", "官方文档", "解决方案", "技术对比"],
    },
    "entity_graph": {
        "name": "知识图谱",
        "description": "查询知识图谱中的实体关系和拓扑结构。适用于：理解概念关系、查找相关组件、分析依赖关系。",
        "best_for": ["概念关系", "组件依赖", "系统架构"],
    },
    "pitfall_advisor": {
        "name": "避坑指南",
        "description": "查询历史踩坑经验和避坑建议。适用于：排查错误、避免常见陷阱、查找修复方案。",
        "best_for": ["排查错误", "避免陷阱", "查找修复方案"],
    },
    "create_artifact": {
        "name": "创作生成",
        "description": "将用户诉求直接创作成结构化交付物（PPTX/DOCX/XLSX/HTML），并导出为物理文件。适用于：做 PPT、写研报/公文、做课件/教案、出对比表、生成可视化看板。",
        "best_for": ["做 PPT", "写研报", "做课件", "出对比表", "生成看板"],
    },
}


# ── Planning Prompt ─────────────────────────────────────────────────────────
_PLANNING_SYSTEM_PROMPT = """你是一个智能助手的规划模块。你的任务是分析用户的问题，并决定应该使用哪些工具来回答。

## 可用工具

{tools_description}

## 规划规则

1. **分析用户意图**：理解用户真正想问什么，而不是表面文字
2. **选择工具**：根据问题类型选择最合适的工具组合
3. **确定顺序**：工具应该按什么顺序执行（有些工具的结果可能影响后续工具的选择）
4. **生成搜索词**：为每个工具生成最有效的搜索查询

## 输出格式

请用 JSON 格式输出你的规划，包含以下字段：

```json
{{
  "analysis": "对用户问题的简要分析（1-2句话）",
  "query_type": "greeting|question|task|troubleshoot|creative|analysis",
  "creative_mode": "仅当 query_type=creative 时填写，创作模式：ppt|doc|lesson|table|web（其余情况省略或留空）",
  "tools": [
    {{
      "tool": "工具名称",
      "reason": "为什么选择这个工具",
      "search_query": "用于搜索的查询词（如果适用）",
      "priority": 1
    }}
  ],
  "expected_output": "预期回答的类型和格式"
}}
```

## 注意事项

- 如果是简单的问候或闲聊，tools 数组可以为空
- 不是所有工具都需要使用，只选择真正有帮助的
- 搜索查询应该简洁有效，避免过于宽泛
- 考虑用户可能的后续问题
- 仅有明确「创作产物」诉求时才设 query_type=creative，普通「总结/解释/对比分析」问答不要误判为 creative

## 创作意图（creative）映射规则

当用户希望「产出一件创作物」（如做 PPT、写研报/公文、做课件/教案、出对比表/矩阵、生成可视化看板）时，query_type 必须为 "creative"，并据此设置 creative_mode：
- 做 PPT / 幻灯片 / 演示文稿 → "ppt"
- 写研报 / 报告 / 公文 / 方案 / 汇报 → "doc"
- 做课件 / 教案 / 课程 / 教学大纲 → "lesson"
- 出对比表 / 矩阵 / 排期表 / 甘特图 → "table"
- 生成可视化看板 / 网页 / HTML 大屏 → "web"
创作意图仍通常需要检索知识库作为素材，tools 中应包含 "knowledge_base"（必要时加 "create_artifact" 表示将产出文件）。
"""


def _build_tools_description() -> str:
    """Build tools description for the planning prompt."""
    lines = []
    for tool_id, tool_info in TOOLS.items():
        lines.append(f"### {tool_info['name']} ({tool_id})")
        lines.append(f"描述：{tool_info['description']}")
        lines.append(f"适用场景：{', '.join(tool_info['best_for'])}")
        lines.append("")
    return "\n".join(lines)


# ── Fast Greeting Detection (no LLM needed) ────────────────────────────────
# 宽松匹配：前后允许空白/标点/emoji，不要求严格锚定
_GREETING_WORDS = (
    '你好', '您好', 'hello', 'hi', 'hey', '嗨',
    '早上好', '下午好', '晚上好', '早安', '晚安',
    '谢谢', '感谢', 'thanks', 'thank',
    'ok', '好的', '收到', '了解', '明白',
    '再见', '拜拜', 'bye', 'goodbye',
)
_GREETING_PATTERN = re.compile(
    r'^[\s!！.。,，?？~～👍👌🙏❤️]*(' + '|'.join(re.escape(w) for w in _GREETING_WORDS) + r')[\s!！.。,，?？~～👍👌🙏❤️]*$',
    re.IGNORECASE
)


def _is_greeting(query: str) -> bool:
    """检测是否为问候/闲聊/简单致谢（宽松匹配）。"""
    q = query.strip().lower()
    if not q:
        return False
    # 1. 正则宽松匹配（含 emoji）
    if _GREETING_PATTERN.match(q):
        return True
    # 2. 纯 emoji / 纯标点 / 纯空白 → 也算问候
    stripped = re.sub(r'[\s!！.。,，?？~～👍👌🙏❤️]+', '', q)
    if not stripped:
        return True
    # 3. 仅问候词+少量其他字符（如 "你好呀"、"hello world" 不算，"你好啊" 算）
    for w in _GREETING_WORDS:
        if q.replace(w, '').strip() in ('', '呀', '啊', '呢', '嘛', '哦', '嗯'):
            return True
    return False


# ── Planning Result ─────────────────────────────────────────────────────────
@dataclass
class ToolPlan:
    """Plan for a single tool invocation."""
    tool: str
    reason: str
    search_query: str | None = None
    priority: int = 1


@dataclass
class AgentPlan:
    """Complete plan for answering a query."""
    analysis: str
    query_type: str
    tools: list[ToolPlan]
    expected_output: str
    is_greeting: bool = False
    creative_mode: str | None = None  # 创作模式：ppt|doc|lesson|table|web（仅 creative 意图）
    
    @property
    def enabled_tools(self) -> list[str]:
        """Return list of tool IDs to activate."""
        return [t.tool for t in sorted(self.tools, key=lambda x: x.priority)]


# ── LLM-based Planner ──────────────────────────────────────────────────────
def plan_with_llm(
    query: str,
    llm_client: LLMClient,
    history: list[dict[str, str]] | None = None,
) -> AgentPlan:
    """Use LLM to plan which tools to use for answering a query.
    
    Args:
        query: User's query text
        llm_client: LLM client for planning
        history: Optional conversation history
        
    Returns:
        AgentPlan with tool selections and reasoning
    """
    # Fast path for greetings (no LLM call needed)
    if _is_greeting(query):
        return AgentPlan(
            analysis="这是一个简单的问候",
            query_type="greeting",
            tools=[],
            expected_output="友好的简短问候回复",
            is_greeting=True,
        )
    
    # Build planning prompt
    tools_desc = _build_tools_description()
    system_prompt = _PLANNING_SYSTEM_PROMPT.format(tools_description=tools_desc)
    
    # Add conversation context if available
    context = ""
    if history:
        recent = history[-4:]  # Last 2 turns
        context = "\n\n## 对话上下文\n"
        for msg in recent:
            role = "用户" if msg["role"] == "user" else "助手"
            context += f"{role}: {msg['content'][:200]}\n"
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请分析以下问题并制定回答计划：\n\n{query}{context}"},
    ]
    
    try:
        # Call LLM for planning (short response, fast)
        response = llm_client._do_chat(
            messages,
            temperature=0.3,  # Low temperature for consistent planning
            max_tokens=500,   # Planning doesn't need long responses
        )
        
        # Parse JSON response
        plan = _parse_planning_response(response, query)
        if plan:
            return plan
            
    except Exception as e:
        logger.warning("LLM planning failed, falling back to regex: %s", e)
    
    # Fallback: use regex-based planning
    return _fallback_regex_plan(query)


def _parse_planning_response(response: str, query: str = "") -> AgentPlan | None:
    """Parse LLM planning response into AgentPlan."""
    try:
        # Extract JSON from response (might be wrapped in markdown code block)
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                return None
        
        data = json.loads(json_str)
        
        tools = []
        for tool_data in data.get("tools", []):
            tools.append(ToolPlan(
                tool=tool_data["tool"],
                reason=tool_data.get("reason", ""),
                search_query=tool_data.get("search_query"),
                priority=tool_data.get("priority", 1),
            ))
        
        # 创作模式下确保 creative_mode 合法，否则用关键词兜底；非创作意图不携带 mode
        creative_mode = data.get("creative_mode")
        if data.get("query_type") == "creative":
            if creative_mode not in CREATIVE_MODES:
                creative_mode = map_creative_mode(query) or "ppt"
        else:
            creative_mode = None
        
        return AgentPlan(
            analysis=data.get("analysis", ""),
            query_type=data.get("query_type", "question"),
            tools=tools,
            expected_output=data.get("expected_output", ""),
            creative_mode=creative_mode,
        )
        
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug("Failed to parse planning response: %s", e)
        return None


def _fallback_regex_plan(query: str) -> AgentPlan:
    """Fallback regex-based planning when LLM fails."""
    q = query.lower()
    
    # Greeting
    if _GREETING_PATTERN.match(query.strip()):
        return AgentPlan(
            analysis="这是一个简单的问候",
            query_type="greeting",
            tools=[],
            expected_output="友好的问候回复",
            is_greeting=True,
        )
    
    # Creative tasks (PPT / 研报 / 课件 / 对比表 / 看板)
    creative_mode = map_creative_mode(q)
    if creative_mode:
        return AgentPlan(
            analysis="这是一个创作类任务，将直接产出结构化交付物",
            query_type="creative",
            tools=[
                ToolPlan("knowledge_base", "检索创作素材", priority=1),
                ToolPlan("entity_graph", "分析知识结构", priority=2),
                ToolPlan("create_artifact", "将生成结构化交付物文件", priority=1),
            ],
            expected_output="结构化创作交付物（PPTX/DOCX/XLSX/HTML）",
            creative_mode=creative_mode,
        )

    # Troubleshooting keywords
    troubleshoot_words = ["报错", "错误", "异常", "失败", "崩溃", "问题", "bug", "error", "exception"]
    if any(word in q for word in troubleshoot_words):
        return AgentPlan(
            analysis="这是一个问题排查类查询",
            query_type="troubleshoot",
            tools=[
                ToolPlan("pitfall_advisor", "查询历史避坑经验", priority=1),
                ToolPlan("knowledge_base", "检索相关文档", priority=2),
                ToolPlan("web_search", "搜索解决方案", priority=3),
            ],
            expected_output="问题诊断和解决方案",
        )
    
    # Code-related keywords
    code_words = ["代码", "函数", "api", "docker", "git", "python", "java", "sql"]
    if any(word in q for word in code_words):
        return AgentPlan(
            analysis="这是一个代码相关查询",
            query_type="task",
            tools=[
                ToolPlan("knowledge_base", "检索代码示例", priority=1),
                ToolPlan("web_search", "搜索技术文档", priority=2),
            ],
            expected_output="代码示例和技术解释",
        )
    
    # Default: comprehensive search
    return AgentPlan(
        analysis="这是一个综合性查询",
        query_type="question",
        tools=[
            ToolPlan("knowledge_base", "检索知识库", priority=1),
            ToolPlan("entity_graph", "分析实体关系", priority=2),
        ],
        expected_output="综合分析回答",
    )
