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
