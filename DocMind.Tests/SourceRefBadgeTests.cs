using DocMind.Models;

namespace DocMind.Tests;

/// <summary>引用分数徽标：按 score_type 量纲选择文案，避免把 RRF 排名分误读为相似度。</summary>
public class SourceRefBadgeTests
{
    [Fact]
    public void LocalScores_MapToTypeSpecificLabels()
    {
        Assert.Equal("相似度 0.71", new SourceRef { Score = 0.71, ScoreType = "vector" }.ScoreBadgeText);
        Assert.Equal("相关度 0.87", new SourceRef { Score = 0.87, ScoreType = "rerank" }.ScoreBadgeText);
        Assert.Equal("关键词匹配 0.83", new SourceRef { Score = 0.83, ScoreType = "bm25" }.ScoreBadgeText);
        Assert.Equal("排名分 0.02", new SourceRef { Score = 0.02, ScoreType = "rrf" }.ScoreBadgeText);
    }

    [Fact]
    public void WebAndAttachment_SkipFakeSimilarity()
    {
        Assert.Equal("网页相关度 45%", new SourceRef { Score = 0.45, ScoreType = "web_relevance", SourceType = "web" }.ScoreBadgeText);
        Assert.Equal("附件全文", new SourceRef { Score = 1.0, ScoreType = "attachment", SourceType = "attachment" }.ScoreBadgeText);
    }

    [Fact]
    public void LegacyData_WithoutScoreType_FallsBackHeuristically()
    {
        // 旧 web 数据 score 恒为 1.0 → 不再显示「相似度 1.00」
        Assert.Equal("网页来源", new SourceRef { Score = 1.0, SourceType = "web", Url = "https://a.b" }.ScoreBadgeText);
        // 旧本地数据 RRF 量纲（< 0.05）→ 标为排名分
        Assert.Equal("排名分 0.02", new SourceRef { Score = 0.02, SourceType = "local" }.ScoreBadgeText);
        // 旧本地数据正常相似度量纲 → 保持相似度文案
        Assert.Equal("相似度 0.71", new SourceRef { Score = 0.71, SourceType = "local" }.ScoreBadgeText);
    }

    [Fact]
    public void DisplayTitle_SanitizesLocalAbsolutePath()
    {
        var src = new SourceRef
        {
            Source = @"C:\Users\Administrator\AppData\Local\kingsoft\cache\manual.pdf",
            Page = 12,
        };
        Assert.Equal("manual.pdf · P12", src.DisplayTitle);
        Assert.DoesNotContain("Administrator", src.DisplayTitle);
    }

    [Fact]
    public void DisplayTitle_DistinguishesSameFileChunksWithoutPage()
    {
        var a = new SourceRef { Source = "doc.pdf", ChunkId = 11, Page = null };
        var b = new SourceRef { Source = "doc.pdf", ChunkId = 22, Page = null };
        Assert.NotEqual(a.DisplayTitle, b.DisplayTitle);
        Assert.Equal("doc.pdf · #11", a.DisplayTitle);
        Assert.Equal("doc.pdf · #22", b.DisplayTitle);
    }

    [Fact]
    public void DisplayTitle_KeepsWebTitleUnchanged()
    {
        var web = new SourceRef
        {
            SourceType = "web",
            Title = "OpenClaw三级记忆系统实现揭秘",
            Url = "https://example.com/a",
        };
        Assert.Equal("OpenClaw三级记忆系统实现揭秘", web.DisplayTitle);
    }

    [Fact]
    public void SanitizeDisplayPath_HandlesUnixAndTrailingSlash()
    {
        Assert.Equal("notes.md", SourceRef.SanitizeDisplayPath("/home/user/docs/notes.md"));
        Assert.Equal("docs", SourceRef.SanitizeDisplayPath("C:/data/docs/"));
    }

    [Fact]
    public void EvidenceSummary_FromSources_CountsLocalAndFetchedWeb()
    {
        var sources = new List<SourceRef>
        {
            new() { SourceType = "local", Source = "a.pdf" },
            new() { SourceType = "local", Source = "b.pdf" },
            new() { SourceType = "web", Url = "https://a", ContentFetched = true },
            new() { SourceType = "web", Url = "https://b", ContentFetched = false },
        };
        var ev = EvidenceSummary.FromSources(sources);
        Assert.Equal(2, ev.LocalCount);
        Assert.Equal(1, ev.WebFetchedCount);
        Assert.Equal(1, ev.WebUnfetchedCount);
        Assert.False(ev.FallbackGeneralKnowledge);
        Assert.False(ev.GraphInjected);
    }

    [Fact]
    public void EvidenceSummary_FromSources_EmptyOrNull()
    {
        Assert.Equal(0, EvidenceSummary.FromSources(null).LocalCount);
        Assert.Equal(0, EvidenceSummary.FromSources([]).LocalCount);
    }
}

/// <summary>证据条文案：库内/网页/图谱/通用知识。</summary>
public class ChatMessageEvidenceTests
{
    [Fact]
    public void EvidenceSummaryText_LocalHit()
    {
        var msg = new DocMind.ViewModels.ChatMessage
        {
            Role = "assistant",
            Evidence = new EvidenceSummary { LocalCount = 5, WebFetchedCount = 1, GraphInjected = true },
            Sources = [new SourceRef { Index = 1, SourceType = "local", Source = "a.pdf" }],
        };
        msg.IsLoading = false;
        msg.IsWaitingForFirstToken = false;
        Assert.Equal("库内原文 5 · 精读网页 1 · 图谱", msg.EvidenceSummaryText);
        Assert.False(msg.HasEvidenceWarning);
        Assert.True(msg.HasEvidenceBar);
    }

    [Fact]
    public void EvidenceSummaryText_FallbackWarning()
    {
        var msg = new DocMind.ViewModels.ChatMessage
        {
            Role = "assistant",
            Evidence = new EvidenceSummary { FallbackGeneralKnowledge = true },
        };
        msg.IsLoading = false;
        msg.IsWaitingForFirstToken = false;
        Assert.Equal("本地未命中 · 基于通用知识", msg.EvidenceSummaryText);
        Assert.True(msg.HasEvidenceWarning);
        Assert.True(msg.HasEvidenceBar);
    }

    [Fact]
    public void EvidenceSummaryText_FallsBackFromSourcesWhenEvidenceMissing()
    {
        // 历史会话：无 evidence 字段，从 Sources 推算
        var msg = new DocMind.ViewModels.ChatMessage
        {
            Role = "assistant",
            Sources =
            [
                new SourceRef { SourceType = "local", Source = "a.pdf" },
                new SourceRef { SourceType = "web", Url = "https://x", ContentFetched = true },
            ],
        };
        msg.IsLoading = false;
        msg.IsWaitingForFirstToken = false;
        Assert.Equal("库内原文 1 · 精读网页 1", msg.EvidenceSummaryText);
        Assert.True(msg.HasEvidenceBar);
    }

    [Fact]
    public void EvidenceBar_ToggleSourcesList()
    {
        var msg = new DocMind.ViewModels.ChatMessage
        {
            Role = "assistant",
            Sources = [new SourceRef { SourceType = "local", Source = "a.pdf" }],
        };
        Assert.True(msg.IsSourcesExpanded);
        Assert.True(msg.ShowSourcesList);
        msg.IsSourcesExpanded = false;
        Assert.False(msg.ShowSourcesList);
        Assert.Equal("展开 1 条来源", msg.EvidenceExpandHint);
    }

    [Fact]
    public void EvidenceBar_ZeroCited_HidesSourcesListAndChevron()
    {
        // 检索到 5 条但答案零实质引用：不铺无用切片列表，chevron 不显示，点击不展开
        var msg = new DocMind.ViewModels.ChatMessage
        {
            Role = "assistant",
            Evidence = new EvidenceSummary
            {
                LocalCount = 5,
                CitationAudit = new CitationAudit { Cited = [1], Valid = [], Invalid = [1], EvidenceSupport = [] },
            },
            Sources = Enumerable.Range(1, 5).Select(i => new SourceRef
                { Index = i, SourceType = "local", Source = $"doc{i}.md" }).ToList(),
        };
        msg.IsLoading = false;
        msg.IsWaitingForFirstToken = false;

        Assert.True(msg.HasSourcesButZeroCited);
        Assert.Equal("库内检索 5 条 · 未作引用", msg.EvidenceSummaryText);
        Assert.False(msg.ShowSourcesList);
        Assert.Equal("", msg.EvidenceExpandHint);

        msg.ToggleEvidenceBarCommand.Execute(null);
        Assert.False(msg.ShowSourcesList);
    }

    [Fact]
    public void EvidenceBar_ZeroCited_RestoresWhenEvidenceSupportAppears()
    {
        // 同样 5 条来源，一旦 audit 判定有实质引用，列表恢复可展开
        var msg = new DocMind.ViewModels.ChatMessage
        {
            Role = "assistant",
            Evidence = new EvidenceSummary
            {
                LocalCount = 5,
                CitationAudit = new CitationAudit { Cited = [1], Valid = [1], EvidenceSupport = [1] },
            },
            Sources = [new SourceRef { Index = 1, SourceType = "local", Source = "a.md" }],
        };
        msg.IsLoading = false;
        msg.IsWaitingForFirstToken = false;

        Assert.False(msg.HasSourcesButZeroCited);
        Assert.True(msg.ShowSourcesList);
        Assert.Equal("收起来源", msg.EvidenceExpandHint);
    }

    [Fact]
    public void EvidenceBar_HiddenWhileLoadingOrUserMessage()
    {
        var loading = new DocMind.ViewModels.ChatMessage
        {
            Role = "assistant",
            Evidence = new EvidenceSummary { LocalCount = 1 },
            IsLoading = true,
        };
        Assert.False(loading.HasEvidenceBar);

        var user = new DocMind.ViewModels.ChatMessage
        {
            Role = "user",
            Content = "hi",
            IsLoading = false,
        };
        Assert.False(user.HasEvidenceBar);
    }

    [Fact]
    public void Sources_Setter_DefensivelyDropsUnfetchedWeb()
    {
        var msg = new DocMind.ViewModels.ChatMessage { Role = "assistant" };
        msg.Sources =
        [
            new SourceRef { Index = 1, SourceType = "local", Source = "a.pdf" },
            new SourceRef { Index = 2, SourceType = "web", Url = "https://x", ContentFetched = false },
            new SourceRef { Index = 3, SourceType = "web", Url = "https://y", ContentFetched = true },
        ];
        Assert.Equal(2, msg.Sources!.Count);
        Assert.DoesNotContain(msg.Sources, s => s.Url == "https://x");
        Assert.Contains(msg.Sources, s => s.Url == "https://y");
    }
}
