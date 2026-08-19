namespace DocMind.Models;

/// <summary>对应后端 SearchHitDTO。</summary>
public sealed record SearchHit
{
    public int Rank { get; init; }
    public double Score { get; init; }
    public string MatchType { get; init; } = string.Empty;
    public double VectorScore { get; init; }
    public double Bm25Score { get; init; }
    public string Source { get; init; } = string.Empty;
    public string Format { get; init; } = string.Empty;
    public int? Page { get; init; }
    public string? Heading { get; init; }
    public string Content { get; init; } = string.Empty;

    /// <summary>去除 note: 等前缀后的友好文件名显示。</summary>
    public string DisplaySource => Source.StartsWith("note:", StringComparison.OrdinalIgnoreCase)
        ? Source.Substring(5)
        : System.IO.Path.GetFileName(Source);

    /// <summary>归一化百分比与中文相关度评级（将 RRF 倒数分或余弦分换算为 0~100% 易懂文案）。</summary>
    public string ScorePercentText
    {
        get
        {
            if (Score <= 0.0) return "0% 相关";
            // 若为 RRF 融合分 (通常在 0.01 ~ 0.035 之间)
            if (Score < 0.1)
            {
                // 顶级 (0.03+) 映射到 90~99%
                double pct = Math.Min(99.0, Math.Max(50.0, (Score / 0.033) * 90.0));
                string label = pct >= 88 ? "极高相关" : (pct >= 75 ? "强相关" : "中度相关");
                return $"{pct:F0}% · {label}";
            }
            // 若为 0~1 余弦相似度
            double directPct = Math.Min(100.0, Score * 100.0);
            string directLabel = directPct >= 85 ? "极高相关" : (directPct >= 70 ? "强相关" : "中度相关");
            return $"{directPct:F0}% · {directLabel}";
        }
    }

    /// <summary>检索匹配引擎徽章文案。</summary>
    public string MatchBadgeText => MatchType.ToLowerInvariant() switch
    {
        "rrf_hybrid" or "both" or "hybrid" => "🔥 双引擎共识",
        "vector" => "🧠 语义关联",
        "bm25" => "🎯 关键词精准",
        _ => string.IsNullOrWhiteSpace(MatchType) ? "🔍 命中" : MatchType,
    };
}

public sealed record SearchResponse
{
    public string Query { get; init; } = string.Empty;
    public IReadOnlyList<SearchHit> Hits { get; init; } = [];
    public int Total { get; init; }
    public int ElapsedMs { get; init; }
    public bool Degraded { get; init; }
}
