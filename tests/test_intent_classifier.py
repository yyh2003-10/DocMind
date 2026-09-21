"""Tests for the agent intent classifier.

Verifies that queries are correctly classified into intent types
and that tool configurations are appropriate for each intent.
Edge cases include mixed languages, very long inputs, empty strings,
and ambiguous queries.
"""

from __future__ import annotations

import pytest

from doc2mind.core.agent.intent import (
    QueryIntent,
    ToolConfig,
    classify_intent,
    get_status_message,
    _looks_like_research,
    map_research_task,
    research_tool_priority,
)


# ── Greeting Tests ──────────────────────────────────────────────────────────
class TestGreetingIntent:
    """Greeting queries should skip all retrieval tools."""

    @pytest.mark.parametrize("query", [
        "你好",
        "您好",
        "hi",
        "hello",
        "hey",
        "嗨",
        "早上好",
        "下午好",
        "晚上好",
        "谢谢",
        "感谢",
        "ok",
        "好的",
        "收到",
        "再见",
        "拜拜",
        "bye",
        "你好！",
        "Hi!",
        "hello.",
    ])
    def test_greeting_patterns(self, query: str) -> None:
        intent, config = classify_intent(query)
        assert intent == QueryIntent.GREETING
        assert config.enable_knowledge_base is False
        assert config.enable_web_search is False
        assert config.enable_entity_graph is False
        assert config.enable_pitfall_advisor is False
        assert config.complexity == 1
        assert config.tool_priority == []

    def test_greeting_with_whitespace(self) -> None:
        intent, _ = classify_intent("  你好  ")
        assert intent == QueryIntent.GREETING


# ── Simple Q&A Tests ────────────────────────────────────────────────────────
class TestSimpleQAIntent:
    """Simple factual questions should only use knowledge base."""

    @pytest.mark.parametrize("query", [
        "什么是机器学习",
        "谁是爱因斯坦",
        "哪个更好",
        "几号放假",
        "多少钱",
        "在哪里买",
        "什么时候开始",
        "是否可以",
    ])
    def test_simple_qa_patterns(self, query: str) -> None:
        intent, config = classify_intent(query)
        assert intent == QueryIntent.SIMPLE_QA
        assert config.enable_knowledge_base is True
        assert config.enable_web_search is False
        assert config.enable_entity_graph is False
        assert config.enable_pitfall_advisor is False
        assert config.complexity == 1


# ── Troubleshoot Tests ──────────────────────────────────────────────────────
class TestTroubleshootIntent:
    """Troubleshooting queries should prioritize pitfall advisor + web search."""

    @pytest.mark.parametrize("query", [
        "这个报错怎么解决",
        "程序异常了",
        "功能失败了",
        "系统崩溃了",
        "API返回错误",
        "nginx 502 error",
        "docker部署失败",
        "数据库连接异常",
    ])
    def test_troubleshoot_patterns(self, query: str) -> None:
        intent, config = classify_intent(query)
        assert intent == QueryIntent.TROUBLESHOOT
        assert config.enable_knowledge_base is True
        assert config.enable_web_search is True
        assert config.enable_pitfall_advisor is True
        assert config.complexity == 3
        # Pitfall should be highest priority
        assert config.tool_priority[0] == "pitfall_advisor"


# ── Code Intent Tests ───────────────────────────────────────────────────────
class TestCodeIntent:
    """Code queries should enable web search for examples/docs."""

    @pytest.mark.parametrize("query", [
        "写一个Python函数",
        "这段代码怎么调试",
        "帮我写个API接口",
        "docker部署流程",
        "git分支管理",
        "正则表达式匹配",
        "JavaScript异步处理",
    ])
    def test_code_patterns(self, query: str) -> None:
        intent, config = classify_intent(query)
        assert intent == QueryIntent.CODE
        assert config.enable_knowledge_base is True
        assert config.enable_web_search is True
        assert config.enable_entity_graph is True
        assert config.enable_pitfall_advisor is True
        assert config.complexity == 2


# ── Creative Intent Tests ───────────────────────────────────────────────────
class TestCreativeIntent:
    """Creative tasks should focus on knowledge base + graph, no web search."""

    @pytest.mark.parametrize("query", [
        "帮我做个PPT",
        "写一份研报",
        "制作幻灯片",
        "生成演示文档",
        "撰写公文",
        "设计模板",
    ])
    def test_creative_patterns(self, query: str) -> None:
        intent, config = classify_intent(query)
        assert intent == QueryIntent.CREATIVE
        assert config.enable_knowledge_base is True
        assert config.enable_web_search is False
        assert config.enable_pitfall_advisor is False
        assert config.complexity == 2


# ── Summary Intent Tests ────────────────────────────────────────────────────
class TestSummaryIntent:
    """Summary requests should use knowledge base + graph only."""

    @pytest.mark.parametrize("query", [
        "概括核心要点",
        "提炼关键信息",
        "梳理一下内容",
        "写个摘要",
    ])
    def test_summary_patterns(self, query: str) -> None:
        intent, config = classify_intent(query)
        assert intent == QueryIntent.SUMMARY
        assert config.enable_knowledge_base is True
        assert config.enable_web_search is False
        assert config.enable_pitfall_advisor is False


# ── Concept Intent Tests ────────────────────────────────────────────────────
class TestConceptIntent:
    """Concept explanations should use KB + web + graph."""

    @pytest.mark.parametrize("query", [
        "什么是微服务架构",
        "解释一下量子计算原理",
        "为什么使用事件驱动",
        "怎么理解DDD",
        "机器学习的本质是什么",
    ])
    def test_concept_patterns(self, query: str) -> None:
        intent, config = classify_intent(query)
        assert intent == QueryIntent.CONCEPT
        assert config.enable_knowledge_base is True
        assert config.enable_web_search is True
        assert config.enable_entity_graph is True


# ── How-to Intent Tests ─────────────────────────────────────────────────────
class TestHowToIntent:
    """How-to instructions should use all tools."""

    @pytest.mark.parametrize("query", [
        "怎么做数据迁移",
        "如何配置Nginx",
        "步骤：安装Redis",
        "入门教程：React",
    ])
    def test_howto_patterns(self, query: str) -> None:
        intent, config = classify_intent(query)
        assert intent == QueryIntent.HOW_TO
        assert config.enable_knowledge_base is True
        assert config.enable_web_search is True
        assert config.enable_pitfall_advisor is True


# ── Complex Analysis (Default) Tests ────────────────────────────────────────
class TestComplexAnalysisIntent:
    """Default/complex queries should use all tools."""

    @pytest.mark.parametrize("query", [
        "对比React和Vue的优缺点",
        "",  # Empty query defaults to complex analysis
    ])
    def test_complex_patterns(self, query: str) -> None:
        intent, config = classify_intent(query)
        assert intent == QueryIntent.COMPLEX_ANALYSIS
        assert config.enable_knowledge_base is True
        assert config.enable_web_search is True
        assert config.enable_entity_graph is True
        assert config.enable_pitfall_advisor is True
        assert config.complexity == 3


# ── Edge Cases ──────────────────────────────────────────────────────────────
class TestEdgeCases:
    """Edge cases: mixed languages, very long inputs, special chars."""

    def test_long_query(self) -> None:
        """Very long query should not crash."""
        long_query = "什么是" + "机器学习" * 100
        intent, config = classify_intent(long_query)
        assert intent == QueryIntent.SIMPLE_QA  # Starts with "什么是"
        assert config.enable_knowledge_base is True

    def test_only_punctuation(self) -> None:
        """Query with only punctuation should default to complex analysis."""
        intent, _ = classify_intent("？？？")
        assert intent == QueryIntent.COMPLEX_ANALYSIS

    def test_only_numbers(self) -> None:
        """Query with only numbers should default to complex analysis."""
        intent, _ = classify_intent("12345")
        assert intent == QueryIntent.COMPLEX_ANALYSIS

    def test_unicode_emoji(self) -> None:
        """Query with emojis should not crash."""
        intent, _ = classify_intent("🔥 这个报错怎么修 🐛")
        assert intent == QueryIntent.TROUBLESHOOT

    def test_html_in_query(self) -> None:
        """Query with HTML tags should not crash."""
        intent, _ = classify_intent("<script>alert('xss')</script>怎么解决")
        assert intent == QueryIntent.TROUBLESHOOT

    def test_very_long_greeting(self) -> None:
        """Greeting pattern should only match short strings."""
        intent, _ = classify_intent("你好" * 50)
        # Long string with repeated greeting - should NOT match greeting pattern
        # because regex uses ^...$ and the repeated text exceeds the pattern
        assert intent != QueryIntent.GREETING or len("你好" * 50) < 20

    def test_case_insensitive_english(self) -> None:
        """English queries should be case-insensitive."""
        intent1, _ = classify_intent("ERROR occurred")
        intent2, _ = classify_intent("error occurred")
        intent3, _ = classify_intent("Error Occurred")
        assert intent1 == intent2 == intent3 == QueryIntent.TROUBLESHOOT

    def test_multiple_intent_keywords(self) -> None:
        """When multiple keywords match, priority should determine intent."""
        # "报错" (troubleshoot) + "代码" (code) -> troubleshoot has higher priority
        intent, _ = classify_intent("代码报错了怎么修")
        assert intent == QueryIntent.TROUBLESHOOT


# ── ToolConfig Tests ────────────────────────────────────────────────────────
class TestToolConfig:
    """Verify ToolConfig defaults and structure."""

    def test_default_config(self) -> None:
        config = ToolConfig()
        assert config.enable_knowledge_base is True
        assert config.enable_web_search is False
        assert config.enable_entity_graph is True
        assert config.enable_pitfall_advisor is True
        assert config.enable_attachments is False
        assert config.complexity == 1
        assert len(config.tool_priority) == 4

    def test_greeting_config_tools_empty(self) -> None:
        _, config = classify_intent("你好")
        assert config.tool_priority == []


# ── get_status_message Tests ────────────────────────────────────────────────
class TestGetStatusMessage:
    """Verify status messages for different intents and tools."""

    def test_greeting_llm_message(self) -> None:
        msg = get_status_message(QueryIntent.GREETING, "llm")
        assert "思考" in msg

    def test_troubleshoot_pitfall_message(self) -> None:
        msg = get_status_message(QueryIntent.TROUBLESHOOT, "pitfall_advisor")
        assert "避坑" in msg

    def test_code_kb_message(self) -> None:
        msg = get_status_message(QueryIntent.CODE, "knowledge_base")
        assert "代码" in msg or "检索" in msg

    def test_unknown_tool_fallback(self) -> None:
        msg = get_status_message(QueryIntent.COMPLEX_ANALYSIS, "unknown_tool")
        assert "处理" in msg or "unknown_tool" in msg

    def test_creative_llm_message(self) -> None:
        msg = get_status_message(QueryIntent.CREATIVE, "llm")
        assert "创作" in msg


# ── Integration: classify_intent + get_status_message ───────────────────────
class TestIntegration:
    """End-to-end tests combining classification and status messages."""

    def test_full_flow_greeting(self) -> None:
        intent, config = classify_intent("你好")
        assert intent == QueryIntent.GREETING
        
        # No tools should be activated
        tools_to_use = []
        if config.enable_entity_graph:
            tools_to_use.append("知识图谱")
        if config.enable_knowledge_base:
            tools_to_use.append("本地知识库")
        if config.enable_pitfall_advisor:
            tools_to_use.append("避坑指南")
        if config.enable_web_search:
            tools_to_use.append("联网搜索")
        
        assert tools_to_use == []
        
        # Should produce a plan message
        intent_desc = {
            QueryIntent.GREETING: "问候寒暄",
        }
        plan_reason = f"识别为「{intent_desc.get(intent, intent.value)}」类问题"
        if tools_to_use:
            plan_reason += f"，将调用 {', '.join(tools_to_use)}"
        else:
            plan_reason += "，无需检索，直接回复"
        
        assert "问候寒暄" in plan_reason
        assert "无需检索" in plan_reason

    def test_full_flow_troubleshoot(self) -> None:
        intent, config = classify_intent("程序崩溃了怎么修")
        assert intent == QueryIntent.TROUBLESHOOT
        
        tools_to_use = []
        if config.enable_entity_graph:
            tools_to_use.append("知识图谱")
        if config.enable_knowledge_base:
            tools_to_use.append("本地知识库")
        if config.enable_pitfall_advisor:
            tools_to_use.append("避坑指南")
        if config.enable_web_search:
            tools_to_use.append("联网搜索")
        
        assert "避坑指南" in tools_to_use
        assert "本地知识库" in tools_to_use
        assert "联网搜索" in tools_to_use
        
        # Status message should be context-appropriate
        pitfall_msg = get_status_message(intent, "pitfall_advisor")
        assert "避坑" in pitfall_msg

    def test_full_flow_simple_qa(self) -> None:
        intent, config = classify_intent("什么是Kubernetes")
        assert intent == QueryIntent.SIMPLE_QA
        
        tools_to_use = []
        if config.enable_entity_graph:
            tools_to_use.append("知识图谱")
        if config.enable_knowledge_base:
            tools_to_use.append("本地知识库")
        if config.enable_pitfall_advisor:
            tools_to_use.append("避坑指南")
        if config.enable_web_search:
            tools_to_use.append("联网搜索")
        
        # Simple Q&A should only use KB
        assert tools_to_use == ["本地知识库"]

# ── Research Intent Tests (M1) ──────────────────────────────────────────────
class TestResearchIntent:
    """科研写作意图识别：强命中 / 多轮弱命中 / 防误判。"""

    # 强命中：文献语境 + 任务词
    @pytest.mark.parametrize("query", [
        "把这些文献的观点对比一下",
        "这几篇论文帮我写个综述",
        "文献集合里有哪些观点对比分析",
        "这些研究资料帮我梳理一下，带引用",
        "帮我写综述大纲，参考文献都用上",
        "这几篇文献的引用支撑怎么写",
    ])
    def test_research_strong_hit(self, query: str) -> None:
        intent, config = classify_intent(query, research_enabled=True)
        assert intent == QueryIntent.RESEARCH
        assert config.enable_knowledge_base is True
        assert config.enable_entity_graph is True
        assert config.enable_web_search is False  # 科研默认不联网

    # 开关关闭时：科研意图回落到常规分支
    @pytest.mark.parametrize("query", [
        "把这些文献的观点对比一下",
        "这几篇论文帮我写个综述",
    ])
    def test_research_disabled_falls_back(self, query: str) -> None:
        intent, _ = classify_intent(query, research_enabled=False)
        assert intent != QueryIntent.RESEARCH

    # 多轮弱命中：本轮仅任务词，历史上文含文献语境
    def test_research_weak_hit_with_history(self) -> None:
        history = [
            {"role": "user", "content": "我这里有 5 篇关于 RAG 的论文"},
            {"role": "assistant", "content": "好的，请告诉我你的需求"},
            {"role": "user", "content": "整理成对比报告"},
        ]
        intent, config = classify_intent("整理成对比报告", history=history, research_enabled=True)
        assert intent == QueryIntent.RESEARCH

    # 防误判：仅「对比」无文献语境且无明确产物 → 不触发
    @pytest.mark.parametrize("query", [
        "对比 A 和 B 的区别",
        "对比一下这两个方案",
        "比较一下苹果和橙子",
    ])
    def test_research_no_false_positive(self, query: str) -> None:
        intent, _ = classify_intent(query, research_enabled=True)
        assert intent != QueryIntent.RESEARCH

    # 独立短语强命中（不依赖任务词）
    @pytest.mark.parametrize("query", [
        "帮我写综述",
        "写综述大纲",
        "文献综述",
        "观点对比一下",
        "对比这些文献",
    ])
    def test_research_literature_only_phrases(self, query: str) -> None:
        intent, _ = classify_intent(query, research_enabled=True)
        assert intent == QueryIntent.RESEARCH


class TestMapResearchTask:
    """科研子任务映射（review / compare / draft）。"""

    @pytest.mark.parametrize("query, expected", [
        ("帮我写个综述", "review"),
        ("写综述大纲", "review"),
        ("文献综述", "review"),
        ("观点对比一下", "compare"),
        ("对比分析这些文献", "compare"),
        ("带引用草稿", "draft"),
        ("引用支撑怎么写", "draft"),
        ("随便聊聊", None),
        ("什么是机器学习", None),
    ])
    def test_map_research_task(self, query: str, expected: str | None) -> None:
        assert map_research_task(query) == expected


class TestResearchToolPriority:
    """科研工具优先级预设。"""

    def test_default_no_web(self) -> None:
        priority = research_tool_priority("写综述", web_crosscheck=False)
        assert priority == ["knowledge_base", "entity_graph"]
        assert "web_search" not in priority

    def test_draft_with_web_crosscheck(self) -> None:
        priority = research_tool_priority("带引用草稿", web_crosscheck=True)
        assert priority == ["knowledge_base", "entity_graph", "web_search"]

    def test_non_draft_no_web_even_with_flag(self) -> None:
        priority = research_tool_priority("写综述", web_crosscheck=True)
        assert priority == ["knowledge_base", "entity_graph"]


class TestResearchStatusMessage:
    """科研意图状态消息。"""

    def test_research_kb_message(self) -> None:
        msg = get_status_message(QueryIntent.RESEARCH, "knowledge_base")
        assert "文献" in msg or "检索" in msg

    def test_research_graph_message(self) -> None:
        msg = get_status_message(QueryIntent.RESEARCH, "entity_graph")
        assert "文献" in msg or "主题" in msg

    def test_research_llm_message(self) -> None:
        msg = get_status_message(QueryIntent.RESEARCH, "llm")
        assert "科研" in msg or "写作" in msg
