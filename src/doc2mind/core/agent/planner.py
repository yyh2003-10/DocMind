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
    from doc2mind.core.config import Settings
    from doc2mind.core.llm.base import LLMClient

# ── 科研 / 闲聊复用（M1：与 arbiter / intent 共享单一来源，防关键词漂移）──
from .intent import _looks_like_research, map_research_task  # noqa: E402
from .arbiter import arbitrate  # noqa: E402

_CHITCHAT_PATTERNS = re.compile(
    r"(随便聊聊|今天天气|给我讲个故事|闲聊|你好吗|在吗|你是谁|你是谁呀)",
    re.IGNORECASE,
)

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
  "query_type": "greeting|question|task|troubleshoot|creative|research|analysis",
  "creative_mode": "仅当 query_type=creative 时填写，创作模式：ppt|doc|lesson|table|web（其余情况省略或留空）",
  "research_task": "仅当 query_type=research 时填写，科研子任务：review|compare|draft（其余情况省略或留空）",
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
- **通识/百科/外部概念类问题（如「什么是 GPT」「介绍一下 Transformer」）必须包含 web_search**：
  这类内容通常不在用户本地知识库中，只检索本地会答非所问或空引用
- 本地项目/私有文档类问题优先 knowledge_base，可不加 web_search
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

## 创作 vs 科研写作裁决规则（M1 新增）

在判定 query_type 时，按下述优先级裁决，避免 creative / research / question 三间摇摆：
- 用户明确要「产出交付物文件」（做 PPT / 写研报 / 出对比表 / 生成看板）→ query_type=creative，creative_mode 必填
- 用户围绕「文献 / 论文 / 几篇资料的综述、观点对比、带引用写作」→ query_type=research，research_task 必填
  （research_task ∈ review（综述/大纲）| compare（观点对比）| draft（带引用草稿））
- 仅是「对比 / 分析」而无明确产物与文献语境 → query_type=question，tools 可含 web_search

## 科研写作（research）工具规则（M1 新增）

- query_type=research 时 tools 必须包含 knowledge_base 且优先（priority 1），entity_graph 其次（priority 2）
- 不要默认联网（web_search），除非确需交叉验证前沿观点
- expected_output 应描述「带 [n] 引用支撑的写作内容」

## 闲聊/无事实诉求（M1 新增）

- 用户仅是闲聊（随便聊聊 / 今天天气 / 给我讲个故事 / 在吗 / 你好吗）或简单致谢 → query_type=question，tools 可为空数组（不强制检索本地库）
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
    research_task: str | None = None  # 科研子任务：review|compare|draft（仅 research 意图，M1 新增）
    degraded: bool = False  # 规划降级标记：True=规则回退产出（LLM 规划不可用，M1 新增）

    @property
    def enabled_tools(self) -> list[str]:
        """Return list of tool IDs to activate."""
        return [t.tool for t in sorted(self.tools, key=lambda x: x.priority)]


# 规划帧展示给用户的类型标签（planner 原文常含英文 meta 指令，不直接透出）
_PLAN_TYPE_LABELS = {
    "greeting": "识别为问候/闲聊",
    "question": "识别为知识问答",
    "task": "识别为任务型查询",
    "troubleshoot": "识别为问题排查",
    "creative": "识别为创作任务",
    "research": "识别为科研写作",
    "analysis": "识别为综合分析",
}

_META_ANALYSIS_PATTERN = re.compile(
    r"(?i)(provide answer|no extra formatting|we need to answer|"
    r"based on the knowledge graph|user didn't specify|just ask)"
)


def user_facing_plan_reason(plan: AgentPlan, tool_names: list[str]) -> str:
    """构造思考区展示的规划说明：中文意图 + 工具链，不透出英文 meta 分析。

    科研写作（research）特殊处理：标题带子任务人话（综述/观点对比/带引用草稿），
    工具链语义区分「本地知识库（文献集合限定） -> 知识图谱（topic 联动）」
    （design 4.B.2 B-2 / 4.B.4，与普通检索文案可区分）。
    """
    # 科研子任务人话映射
    research_sub_labels = {
        "review": "综述/大纲",
        "compare": "观点对比",
        "draft": "带引用草稿",
    }

    if plan.query_type == "research":
        sub = research_sub_labels.get(plan.research_task, plan.research_task or "")
        head = f"识别为科研写作（{sub}）" if sub else "识别为科研写作"
        # 科研工具链语义：把 knowledge_base/entity_graph 包装为「文献集合限定」/「topic 联动」
        display_chain = []
        for name in tool_names:
            if name == "knowledge_base":
                display_chain.append("本地知识库（文献集合限定）")
            elif name == "entity_graph":
                display_chain.append("知识图谱（topic 联动）")
            else:
                display_chain.append(name)
        if display_chain:
            return f"{head}\n\n将调用：" + " -> ".join(display_chain)
        return f"{head}\n\n无需检索，直接回复"

    analysis = (plan.analysis or "").strip()
    looks_like_meta = bool(_META_ANALYSIS_PATTERN.search(analysis))
    has_cjk = bool(re.search(r"[一-鿿]", analysis))
    if analysis and len(analysis) <= 80 and has_cjk and not looks_like_meta:
        head = analysis.split("\n", 1)[0].strip()
    else:
        head = _PLAN_TYPE_LABELS.get(plan.query_type, "已分析用户问题")
    if tool_names:
        return f"{head}\n\n将调用：" + " -> ".join(tool_names)
    return f"{head}\n\n无需检索，直接回复"


# ── LLM-based Planner ──────────────────────────────────────────────────────
def plan_with_llm(
    query: str,
    llm_client: LLMClient,
    history: list[dict[str, str]] | None = None,
    settings: Settings | None = None,
) -> AgentPlan:
    """Use LLM to plan which tools to use for answering a query.
    
    Args:
        query: User's query text
        llm_client: LLM client for planning
        history: Optional conversation history
        settings: 运行时配置（M1 新增，用于仲裁接入三档 intent_conflict_arbitration；
            为 None 时不启用仲裁，保持旧行为）
        
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
            # M1：仲裁复核（仅 settings 提供时启用）
            if settings is not None:
                plan = _apply_arbitration(query, history, plan, llm_client, settings)
            return plan
            
    except Exception as e:
        logger.warning("LLM planning failed, falling back to regex: %s", e)
    
    # Fallback: use regex-based planning（degraded 标记）
    plan = _fallback_regex_plan(query)
    plan.degraded = True
    logger.warning("agent planning degraded to regex, query_type=%s, query=%s", plan.query_type, query[:80])
    return plan


def _apply_arbitration(
    query: str,
    history: list[dict[str, str]] | None,
    plan: AgentPlan,
    llm_client: LLMClient,
    settings: Settings,
) -> AgentPlan:
    """将仲裁结果回填到 AgentPlan（M1 新增，design 决策 A-3/B-4 接入点）。

    仲裁器是「LLM 规划帧」与「正则回退帧」之间的裁决与修正层：
    - LLM 初判非三角区（troubleshoot/summary/task 等）→ 直接沿用，零干预；
    - 三角区（creative/research/question）内由 rules/llm/none 三档门控；
    - research 结果受 intent_research_enabled 门控（关闭时回落 question）。

    返回的 plan 不改变 response shape（query_type/research_task 为既有或追加字段）。
    """
    try:
        result = arbitrate(query, history, plan, llm_client, settings)
        if result.decider == "l0_regex" or result.decider == "l1_score" or result.decider == "l2_llm":
            # 仲裁有明确裁决 → 回填 query_type / research_task
            plan.query_type = result.winning
            if result.winning == "research":
                plan.research_task = result.sub_type or plan.research_task or "review"
            elif result.winning == "creative":
                plan.creative_mode = result.sub_type or plan.creative_mode
            else:
                plan.research_task = None  # question/greeting 不携带科研子类
    except Exception as e:
        logger.debug("Arbitration failed, falling back to LLM plan: %s", e)
    return plan


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

        # 科研模式下解析 research_task（review/compare/draft），关键词兜底；非科研不携带
        research_task = data.get("research_task")
        if data.get("query_type") == "research":
            if research_task not in ("review", "compare", "draft"):
                research_task = map_research_task(query) or "review"
        else:
            research_task = None

        return AgentPlan(
            analysis=data.get("analysis", ""),
            query_type=data.get("query_type", "question"),
            tools=tools,
            expected_output=data.get("expected_output", ""),
            creative_mode=creative_mode,
            research_task=research_task,
            degraded=False,
        )
        
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug("Failed to parse planning response: %s", e)
        return None


def build_research_plan(
    query: str,
    research_task: str | None = None,
    literature_collections: list[str] | None = None,
    settings: Settings | None = None,
) -> AgentPlan:
    """构造科研写作工具链（design 4.B.3 决策 B-1）。

    Args:
        query: 用户查询文本
        research_task: 科研子任务 review|compare|draft；None 时用关键词映射兜底
        literature_collections: 文献集合限定（由入参完成，不修改搜索词）
        settings: 运行时配置（读取 research_web_crosscheck）

    Returns:
        AgentPlan：query_type="research"，工具链顺序固定
        knowledge_base(priority=1) → entity_graph(priority=2) → web_search(priority=3，可选)

    注意：工具 id 不新增（复用既有 5 工具）；集合限定在检索入参完成，不动搜索词；
    向用户展示的工具链命名保持既有语义（design 4.B.1 末段）。
    """
    task = research_task or map_research_task(query) or "review"
    tools = [
        ToolPlan("knowledge_base", "检索所选文献集合的原著切片", priority=1, search_query=query),
        ToolPlan("entity_graph", "联动图谱 topic 层补充观点关联", priority=2, search_query=query),
    ]
    web_crosscheck = getattr(settings, "research_web_crosscheck", False) if settings is not None else False
    if task == "draft" and web_crosscheck:
        tools.append(ToolPlan("web_search", "交叉验证前沿观点（可选）", priority=3))
    analysis = f"科研写作任务：{query[:60]}"
    if literature_collections:
        analysis += f"（限定文献集合 {len(literature_collections)} 个）"
    return AgentPlan(
        analysis=analysis,
        query_type="research",
        research_task=task,
        tools=tools,
        expected_output="带 [n] 引用支撑的写作内容",
    )


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

    # 闲聊 / 泛问兜底（design 决策 A-4）：不强制检索本地库、无来源编号
    if _CHITCHAT_PATTERNS.search(query):
        return AgentPlan(
            analysis="这是闲聊或无事实诉求",
            query_type="question",
            tools=[],
            expected_output="友好的闲聊回复",
        )

    # 科研写作（design 决策 B-4：降级路径与 LLM 路径输出同一契约）
    if _looks_like_research(query):
        return build_research_plan(query, research_task=map_research_task(query) or "review")
    
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

    # Troubleshooting keywords（与 intent._TROUBLESHOOT_PATTERNS 语义对齐，补口语词；
# 不含泛化的「解决/修复」——这些词单独出现不能定性排障，避免误判普通问答）
    troubleshoot_words = ["报错", "错误", "异常", "失败", "崩溃", "卡住", "闪退", "死锁",
                          "问题", "bug", "error", "exception", "故障"]
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
    # 必须带上 web_search：LLM 规划失败落到此回退时，若默认不含联网，
    # 像「你知道 GPT 吗」这类通识题会在本地库空命中后干瞪眼，无法自主上网找。
    return AgentPlan(
        analysis="这是一个综合性查询",
        query_type="question",
        tools=[
            ToolPlan("knowledge_base", "检索知识库", priority=1),
            ToolPlan("entity_graph", "分析实体关系", priority=2),
            ToolPlan("web_search", "联网补充公开资料", priority=3),
        ],
        expected_output="综合分析回答",
    )
