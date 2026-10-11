using DocMind.ViewModels;
using Xunit;

namespace DocMind.Tests;

/// <summary>ThinkingStep：后端「正在…/✔/⚠/✖」文本协议解析为紧凑 pill。</summary>
public class ThinkingStepTests
{
    [Fact]
    public void Parse_RunningLine()
    {
        var step = ThinkingStep.Parse("正在检索知识库...");
        Assert.Equal(ThinkingStepKind.Running, step.Kind);
        Assert.True(step.IsRunning);
        Assert.Equal("正在检索知识库...", step.Summary);
        Assert.False(step.HasDetail);
    }

    [Fact]
    public void Parse_LocalHitDone_ConvergesToCompactSummary()
    {
        var step = ThinkingStep.Parse("✔ 检索知识库：命中 5 个分块（已重排精排）（a.md、b.md、c.md、d.md）");
        Assert.Equal(ThinkingStepKind.Done, step.Kind);
        Assert.Equal("库内 · 5 个来源", step.Summary);
        Assert.Contains("a.md", step.Detail);
        Assert.True(step.HasDetail);
        Assert.False(step.IsRunning);
    }

    [Fact]
    public void Parse_LocalHitAndCite_ShowsHonestCounts()
    {
        var ok = ThinkingStep.Parse("✔ 检索知识库：命中 3 个分块 · 可引用 3（a.md）");
        Assert.Equal("库内命中 3 · 可引用 3", ok.Summary);

        var zero = ThinkingStep.Parse("✔ 检索知识库：命中 5 个分块 · 可引用 0（低分丢弃 3 / 主题不符 2 / 降为背景 0）（x.md）");
        Assert.Equal("库内命中 5 · 可引用 0", zero.Summary);
    }

    [Fact]
    public void Parse_DefinitionStrategyPill()
    {
        var step = ThinkingStep.Parse("✔ 回答策略：定义题结构化（deep_qa（可引用 2 / 库内 0 / 深度联网 True））");
        Assert.Equal("定义题结构化", step.Summary);
    }

    [Fact]
    public void Parse_LocalMiss()
    {
        var step = ThinkingStep.Parse("✔ 检索知识库：未命中本地分块");
        Assert.Equal(ThinkingStepKind.Done, step.Kind);
        Assert.Equal("库内 · 未命中", step.Summary);
    }

    [Fact]
    public void Parse_EntityRelations()
    {
        var hit = ThinkingStep.Parse("✔ 实体关系：找到 2 条与本轮主题相关的知识拓扑");
        Assert.Equal("实体关系 · 2 条", hit.Summary);

        var none = ThinkingStep.Parse("✔ 实体关系：未发现与本轮主题相关的图谱关联");
        Assert.Equal("实体关系 · 无关联", none.Summary);
    }

    [Fact]
    public void Parse_Pitfall()
    {
        var none = ThinkingStep.Parse("✔ 避坑指南：未发现相关历史避坑经验");
        Assert.Equal("避坑 · 无历史", none.Summary);

        var hit = ThinkingStep.Parse("✔ 避坑指南：找到 3 条经验（《a.md》, 《b.md》）");
        Assert.Equal("避坑 · 3 条", hit.Summary);
    }

    [Fact]
    public void Parse_WebSearch()
    {
        var none = ThinkingStep.Parse("✔ 联网搜索：无需联网检索或未检索到高相关页面");
        Assert.Equal("联网 · 无结果", none.Summary);
    }

    [Fact]
    public void Parse_AgentToolAndRescue_Summary()
    {
        var tool = ThinkingStep.Parse("调用工具 web_search");
        Assert.Equal("工具 · 联网搜索", tool.Summary);
        var kb = ThinkingStep.Parse("调用工具 kb_search");
        Assert.Equal("工具 · 知识库", kb.Summary);
        var plan = ThinkingStep.Parse("Agent 规划：knowledge_base + web_search");
        Assert.Equal("Agent 规划", plan.Summary);
        var rescue = ThinkingStep.Parse("✔ 联网证据偏弱（可引用 1），追加改写检索：「挠度 定义...」");
        Assert.Equal("联网 · 补搜", rescue.Summary);
        var modeAgent = ThinkingStep.Parse("✔ 回答模式：Agent 工具循环 · 自动模式：深度联网 + 研究/定义型问题 → Agent 工具循环");
        Assert.Equal("模式 · Agent", modeAgent.Summary);
        var modeRag = ThinkingStep.Parse("✔ 回答模式：RAG 知识库问答 · 用户指定 RAG 模式");
        Assert.Equal("模式 · RAG", modeRag.Summary);
        var modeDegrade = ThinkingStep.Parse("⚠ 回答模式：RAG 知识库问答 · 后端未开启 Agent（agent_mode_enabled=false），本轮回落 RAG");
        Assert.Equal("模式 · 回落 RAG", modeDegrade.Summary);
    }

    [Fact]
    public void Parse_WarnLine_KeepsFullTextAsSummaryAndDetail()
    {
        var step = ThinkingStep.Parse("⚠ 检索降级：重排模型不可用（加载失败）");
        Assert.Equal(ThinkingStepKind.Warn, step.Kind);
        Assert.True(step.IsBadged);
        Assert.Contains("重排模型不可用", step.Summary);
        Assert.Equal(step.Summary, step.Detail);
    }

    [Fact]
    public void Parse_FailLine()
    {
        var step = ThinkingStep.Parse("✖ 回答生成失败：404");
        Assert.Equal(ThinkingStepKind.Fail, step.Kind);
        Assert.True(step.IsBadged);
        Assert.Contains("回答生成失败", step.Summary);
        Assert.Contains("404", step.Summary);
    }

    [Fact]
    public void Parse_UnrecognizedLine_DegradesToPlainSummaryWithoutLoss()
    {
        var raw = "本地知识库命中质量不足（无本地命中），自动联网补充公开资料...";
        var step = ThinkingStep.Parse(raw);
        Assert.Equal(raw, step.Summary);
        Assert.Equal(ThinkingStepKind.Done, step.Kind);
    }

    [Fact]
    public void Parse_EmptyOrNull()
    {
        Assert.Equal("", ThinkingStep.Parse("").Summary);
        Assert.Equal("", ThinkingStep.Parse(null!).Summary);
    }

    [Fact]
    public void ToggleCommand_OnlyTogglesWhenDetailExists()
    {
        var withDetail = ThinkingStep.Parse("✔ 检索知识库：命中 5 个分块（a.md）");
        withDetail.ToggleCommand.Execute(null);
        Assert.True(withDetail.IsExpanded);
        withDetail.ToggleCommand.Execute(null);
        Assert.False(withDetail.IsExpanded);

        var noDetail = ThinkingStep.Parse("正在生成回答...");
        noDetail.ToggleCommand.Execute(null);
        Assert.False(noDetail.IsExpanded);
    }

    [Fact]
    public void Parse_LlmMappingUnavailable_CompactsToWarn()
    {
        var step = ThinkingStep.Parse("已使用或映射（LLM 映射不可用）");
        Assert.Equal(ThinkingStepKind.Warn, step.Kind);
        Assert.True(step.IsDegraded);
        Assert.Equal("LLM 映射不可用", step.Summary);
        Assert.Contains("映射不可用", step.Detail);
        Assert.True(step.HasDetail);
    }

    [Fact]
    public void Parse_NoChangeResult_IsNoiseDone()
    {
        var step = ThinkingStep.Parse("✔ 内存表：无变化");
        Assert.Equal(ThinkingStepKind.Done, step.Kind);
        Assert.True(step.IsNoise);
        Assert.False(step.IsDegraded);
    }

    [Fact]
    public void Parse_AgentToolResultNoise_IsCompacted()
    {
        var step = ThinkingStep.Parse("✔ 工具结果 kb_search: 无变化");
        Assert.True(step.IsNoise);
        Assert.Equal(ThinkingStepKind.Done, step.Kind);
    }

    [Fact]
    public void Parse_UnknownDegradedLine_UsesWarnAndShortSummary()
    {
        var step = ThinkingStep.Parse("知识库检索 | 知识提取 | 文本投影（LLM 结果不可用）");
        Assert.Equal(ThinkingStepKind.Warn, step.Kind);
        Assert.True(step.IsDegraded);
        Assert.Equal("LLM 结果不可用", step.Summary);
    }
}

/// <summary>ChatMessage.ThinkingStepPills：与 ThinkingSteps 的同步收敛行为。</summary>
public class ChatMessageThinkingPillTests
{
    [Fact]
    public void AddThinkingStep_RunningThenDone_ReplacesPill()
    {
        var msg = new DocMind.ViewModels.ChatMessage { Role = "assistant" };
        msg.AddThinkingStep("正在检索知识库...");
        msg.AddThinkingStep("✔ 检索知识库：命中 5 个分块（a.md）");

        // 「✔」替换「正在…」原文行（替换语义，非追加）
        Assert.Single(msg.ThinkingSteps);
        Assert.Single(msg.ThinkingStepPills);
        var pill = msg.ThinkingStepPills[0];
        Assert.False(pill.IsRunning);
        Assert.Equal(ThinkingStepKind.Done, pill.Kind);
        Assert.Equal("库内 · 5 个来源", pill.Summary);
    }

    [Fact]
    public void AddThinkingStep_IndependentSteps_AppendPills()
    {
        var msg = new DocMind.ViewModels.ChatMessage { Role = "assistant" };
        msg.AddThinkingStep("正在检索知识库...");
        msg.AddThinkingStep("✔ 检索知识库：未命中本地分块");
        msg.AddThinkingStep("正在联网搜索...");

        // ✔ 替换第一条进行中 pill，第三条为新的进行中 pill
        Assert.Equal(2, msg.ThinkingStepPills.Count);
        Assert.True(msg.ThinkingStepPills[^1].IsRunning);
    }

    [Fact]
    public void FailThinkingStep_ReplacesRunningPillWithFail()
    {
        var msg = new DocMind.ViewModels.ChatMessage { Role = "assistant" };
        msg.AddThinkingStep("正在检索知识库...");
        msg.FailThinkingStep("404", "回答生成失败");

        Assert.Single(msg.ThinkingStepPills);
        var pill = msg.ThinkingStepPills[0];
        Assert.Equal(ThinkingStepKind.Fail, pill.Kind);
        Assert.True(pill.IsBadged);
        Assert.Contains("404", pill.Summary);
    }
}
