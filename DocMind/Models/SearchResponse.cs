namespace DocMind.Models;

/// <summary>对应后端 SearchHitDTO。</summary>
public sealed record SearchHit
{
    public int Rank { get; init; }
    public double Score { get; init; }
    public string MatchType { get; init; } = string.Empty;
    public double VectorScore { get; init; }
    public double Bm25Score { get; init; }
    /// <summary>重排分（后端 sigmoid 归一化 0-1）；null=未启用重排。</summary>
    public double? RerankScore { get; init; }
    public string Source { get; init; } = string.Empty;
    public string Format { get; init; } = string.Empty;
    public int? Page { get; init; }
    public string? Heading { get; init; }
    public string Content { get; init; } = string.Empty;

    /// <summary>去除 note: 等前缀后的友好文件名显示。</summary>
    public string DisplaySource => Source.StartsWith("note:", StringComparison.OrdinalIgnoreCase)
        ? Source.Substring(5)
        : System.IO.Path.GetFileName(Source);

    /// <summary>用于进度条/条形/列表徽标的真实相关度：优先取重排分（最精确），
    /// 其次按匹配类型取分量分；RRF 融合分仅用于排序，不参与展示。</summary>
    public double DisplayScore =>
        RerankScore is double r ? r
        : MatchType.ToLowerInvariant() switch { "bm25" => Bm25Score, _ => VectorScore };

    /// <summary>归一化百分比与中文相关度评级（基于真实分量分，不再把 RRF 排名分美化）。</summary>
    public string ScorePercentText
    {
        get
        {
            // 用真实分量分（向量相似度/BM25/重排概率，0-1），不再把 RRF 排名分
            // （≤0.033）美化成 50%~99% 伪百分比——垃圾结果也会显示「50% 相关」
            double s = DisplayScore;
            if (s <= 0.0) return "0% 相关";
            double pct = Math.Min(100.0, s * 100.0);
            string label = pct >= 85 ? "极高相关"
                : pct >= 70 ? "强相关"
                : pct >= 50 ? "中度相关"
                : "弱相关";
            return $"{pct:F0}% · {label}";
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
    /// <summary>后端针对空结果或降级检索返回的具体提示。</summary>
    public string? Message { get; init; }
}
