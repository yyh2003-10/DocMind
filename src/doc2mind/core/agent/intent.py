"""Query intent classifier for adaptive RAG pipeline routing.

Classifies user queries into intent types and determines which tools
(retrieval, web search, entity graph, pitfall advisor) to activate.
This makes the system behave more like an intelligent agent rather than
a fixed pipeline search engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class QueryIntent(str, Enum):
    """Query intent types."""
    GREETING = "greeting"           # Hello, hi, thanks, etc.
    SIMPLE_QA = "simple_qa"         # Simple factual questions
    COMPLEX_ANALYSIS = "complex_analysis"  # Deep analysis, comparison
    CODE = "code"                   # Code-related questions
    CREATIVE = "creative"           # Creative tasks (PPT, doc, etc.)
    TROUBLESHOOT = "troubleshoot"   # Error/bug/troubleshooting
    SUMMARY = "summary"             # Summarization requests
    CONCEPT = "concept"             # Concept explanation
    HOW_TO = "how_to"              # How-to instructions
    OPINION = "opinion"             # Opinion/recommendation requests


@dataclass
class ToolConfig:
    """Configuration for which tools to activate."""
    enable_knowledge_base: bool = True
    enable_web_search: bool = False
    enable_entity_graph: bool = True
    enable_pitfall_advisor: bool = True
    enable_attachments: bool = False
    # Priority: lower = more important for this query
    tool_priority: list[str] = field(default_factory=lambda: [
        "knowledge_base", "entity_graph", "pitfall_advisor", "web_search"
    ])
    # Estimated complexity for multi-step planning
    complexity: int = 1  # 1=simple, 2=moderate, 3=complex


# Intent patterns (compiled once for performance)
_GREETING_PATTERNS = re.compile(
    r'^(你好|您好|hi|hello|hey|嗨|早上好|下午好|晚上好|谢谢|感谢|ok|好的|收到|再见|拜拜|bye)[\s!！.。]*$',
    re.IGNORECASE
)

_SIMPLE_QA_PATTERNS = re.compile(
    r'^(什么是|谁是|哪个|几号|多少|在哪里|什么时候|是否|能不能|可不可以)',
    re.IGNORECASE
)

_CODE_PATTERNS = re.compile(
    r'(代码|函数|方法|api|接口|类|变量|循环|递归|调试|debug|编译|运行|部署|docker|git|npm|pip|python|java|javascript|typescript|csharp|sql|html|css|正则|regex|算法|数据结构)',
    re.IGNORECASE
)

_TROUBLESHOOT_PATTERNS = re.compile(
    r'(报错|错误|异常|失败|崩溃|卡住|不工作|不行|无法|不能|问题|bug|error|exception|crash|fail|broken|issue|故障|排查|修复|解决|优化)',
    re.IGNORECASE
)

_CREATIVE_PATTERNS = re.compile(
    r'(ppt|幻灯片|演示|课件|研报|报告|公文|文档|总结|汇报|方案|规划|设计|模板|创作|撰写|编写|生成.*文档|生成.*ppt|word|excel|xlsx)',
    re.IGNORECASE
)

# 创作意图关键词 → 创作模式（persona）的有序映射，供 planner 复用，避免两处关键词漂移。
# 顺序即优先级：PPT 类最先，最后是泛创作兜底。
# 覆盖用户直接点名的文件格式：PPT(演示) / Word(文档) / Excel(表格)。
_CREATIVE_MODE_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("ppt", ("ppt", "幻灯片", "演示", "slide")),
    ("lesson", ("课件", "教案", "课程", "教学", "lesson")),
    ("table", ("对比", "矩阵", "排期", "甘特", "matrix", "gantt", "表格", "excel", "xlsx")),
    ("web", ("看板", "网页", "html", "dashboard", "web", "大屏")),
    ("doc", ("研报", "报告", "公文", "方案", "汇报", "doc", "word", "report", "立项", "标书")),
]


def map_creative_mode(text: str) -> str | None:
    """将创作类自然语言映射到创作模式（persona 类型）。

    Args:
        text: 用户查询文本

    Returns:
        "ppt" | "doc" | "lesson" | "table" | "web" 之一；
        若文本不含明确创作意图则返回 None。

    注意：仅识别「明确创作动词 + 产物名词」（做 PPT / 写研报 / 出对比表 等），
    不包含「总结 / 规划 / 设计 / 模板」等模糊词，避免与 SUMMARY / CONCEPT 意图误路由。
    """
    if not text:
        return None
    t = text.lower()
    for mode, keywords in _CREATIVE_MODE_MAP:
        if any(k in t for k in keywords):
            return mode
    # 泛创作动词兜底（明确的「创作 / 撰写 / 生成文档」等），默认产出 PPT（最常见诉求）
    if any(k in t for k in ("创作", "撰写", "编写", "生成文档", "生成ppt", "出文档", "写个文档")):
        return "ppt"
    return None


_SUMMARY_PATTERNS = re.compile(
    r'(总结|概括|归纳|提炼|梳理|摘要|要点|关键|核心|精华|浓缩)',
    re.IGNORECASE
)

_CONCEPT_PATTERNS = re.compile(
    r'(概念|原理|机制|架构|模式|设计|理论|思想|哲学|本质|底层|深层|为什么|为何|怎么理解|意味着)',
    re.IGNORECASE
)

_HOW_TO_PATTERNS = re.compile(
    r'(怎么做|如何|怎么|步骤|流程|指南|教程|入门|快速上手|实操|实践|操作)',
    re.IGNORECASE
)


def classify_intent(query: str, history: list[dict[str, str]] | None = None) -> tuple[QueryIntent, ToolConfig]:
    """Classify query intent and determine tool configuration.
    
    Args:
        query: User's query text
        history: Optional conversation history for context
        
    Returns:
        Tuple of (QueryIntent, ToolConfig)
    """
    q = query.strip()
    
    # 1. Check for greeting
    if _GREETING_PATTERNS.match(q):
        return QueryIntent.GREETING, ToolConfig(
            enable_knowledge_base=False,
            enable_web_search=False,
            enable_entity_graph=False,
            enable_pitfall_advisor=False,
            complexity=1,
            tool_priority=[],
        )
    
    # 2. Check for troubleshooting (highest priority - often needs web search)
    if _TROUBLESHOOT_PATTERNS.search(q):
        return QueryIntent.TROUBLESHOOT, ToolConfig(
            enable_knowledge_base=True,
            enable_web_search=True,  # Often needs latest solutions
            enable_entity_graph=True,
            enable_pitfall_advisor=True,  # Critical for troubleshooting
            complexity=3,
            tool_priority=["pitfall_advisor", "knowledge_base", "web_search", "entity_graph"],
        )
    
    # 3. Check for creative tasks
    if _CREATIVE_PATTERNS.search(q):
        return QueryIntent.CREATIVE, ToolConfig(
            enable_knowledge_base=True,
            enable_web_search=False,  # Creative tasks use internal knowledge
            enable_entity_graph=True,
            enable_pitfall_advisor=False,
            complexity=2,
            tool_priority=["knowledge_base", "entity_graph"],
        )
    
    # 4. Check for code-related queries
    if _CODE_PATTERNS.search(q):
        return QueryIntent.CODE, ToolConfig(
            enable_knowledge_base=True,
            enable_web_search=True,  # Code often needs examples/docs
            enable_entity_graph=True,
            enable_pitfall_advisor=True,  # Code pitfalls are important
            complexity=2,
            tool_priority=["knowledge_base", "web_search", "entity_graph", "pitfall_advisor"],
        )
    
    # 5. Check for summary requests
    if _SUMMARY_PATTERNS.search(q):
        return QueryIntent.SUMMARY, ToolConfig(
            enable_knowledge_base=True,
            enable_web_search=False,
            enable_entity_graph=True,
            enable_pitfall_advisor=False,
            complexity=2,
            tool_priority=["knowledge_base", "entity_graph"],
        )
    
    # 6. Check for concept explanation
    if _CONCEPT_PATTERNS.search(q):
        return QueryIntent.CONCEPT, ToolConfig(
            enable_knowledge_base=True,
            enable_web_search=True,  # Concepts may need external references
            enable_entity_graph=True,
            enable_pitfall_advisor=False,
            complexity=2,
            tool_priority=["knowledge_base", "entity_graph", "web_search"],
        )
    
    # 7. Check for how-to instructions
    if _HOW_TO_PATTERNS.search(q):
        return QueryIntent.HOW_TO, ToolConfig(
            enable_knowledge_base=True,
            enable_web_search=True,
            enable_entity_graph=True,
            enable_pitfall_advisor=True,
            complexity=2,
            tool_priority=["knowledge_base", "web_search", "entity_graph", "pitfall_advisor"],
        )
    
    # 8. Check for simple questions
    if _SIMPLE_QA_PATTERNS.match(q):
        return QueryIntent.SIMPLE_QA, ToolConfig(
            enable_knowledge_base=True,
            enable_web_search=False,
            enable_entity_graph=False,  # Simple Q&A doesn't need graph
            enable_pitfall_advisor=False,
            complexity=1,
            tool_priority=["knowledge_base"],
        )
    
    # 9. Default: complex analysis (most comprehensive)
    return QueryIntent.COMPLEX_ANALYSIS, ToolConfig(
        enable_knowledge_base=True,
        enable_web_search=True,
        enable_entity_graph=True,
        enable_pitfall_advisor=True,
        complexity=3,
        tool_priority=["knowledge_base", "entity_graph", "web_search", "pitfall_advisor"],
    )


def get_status_message(intent: QueryIntent, tool_name: str) -> str:
    """Get user-friendly status message for tool activation.
    
    Args:
        intent: The classified query intent
        tool_name: Name of the tool being executed
        
    Returns:
        Status message string
    """
    messages = {
        QueryIntent.GREETING: {
            "llm": "正在思考回复...",
        },
        QueryIntent.SIMPLE_QA: {
            "knowledge_base": "正在检索知识库...",
            "llm": "正在生成回答...",
        },
        QueryIntent.COMPLEX_ANALYSIS: {
            "entity_graph": "正在分析实体关系...",
            "knowledge_base": "正在深度检索知识库...",
            "pitfall_advisor": "正在查询避坑指南...",
            "web_search": "正在联网搜索补充资料...",
            "llm": "正在综合分析并生成回答...",
        },
        QueryIntent.CODE: {
            "knowledge_base": "正在检索代码示例...",
            "web_search": "正在搜索技术文档...",
            "entity_graph": "正在分析代码依赖关系...",
            "pitfall_advisor": "正在查询常见陷阱...",
            "llm": "正在生成代码解答...",
        },
        QueryIntent.TROUBLESHOOT: {
            "pitfall_advisor": "正在查询历史避坑经验...",
            "knowledge_base": "正在检索相关文档...",
            "web_search": "正在搜索解决方案...",
            "entity_graph": "正在分析关联组件...",
            "llm": "正在诊断问题并生成解决方案...",
        },
        QueryIntent.CREATIVE: {
            "knowledge_base": "正在检索创作素材...",
            "entity_graph": "正在分析知识结构...",
            "llm": "正在创作内容...",
        },
        QueryIntent.SUMMARY: {
            "knowledge_base": "正在检索需要总结的内容...",
            "entity_graph": "正在分析知识关联...",
            "llm": "正在提炼核心要点...",
        },
        QueryIntent.CONCEPT: {
            "knowledge_base": "正在检索概念解释...",
            "entity_graph": "正在分析概念关系...",
            "web_search": "正在搜索权威解释...",
            "llm": "正在深入剖析概念...",
        },
        QueryIntent.HOW_TO: {
            "knowledge_base": "正在检索操作指南...",
            "web_search": "正在搜索实践教程...",
            "entity_graph": "正在分析相关组件...",
            "pitfall_advisor": "正在查询注意事项...",
            "llm": "正在整理操作步骤...",
        },
    }
    
    intent_msgs = messages.get(intent, messages[QueryIntent.COMPLEX_ANALYSIS])
    return intent_msgs.get(tool_name, f"正在处理{tool_name}...")
