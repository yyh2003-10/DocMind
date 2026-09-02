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
}
