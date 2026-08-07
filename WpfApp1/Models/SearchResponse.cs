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
}

public sealed record SearchResponse
{
    public string Query { get; init; } = string.Empty;
    public IReadOnlyList<SearchHit> Hits { get; init; } = [];
    public int Total { get; init; }
    public int ElapsedMs { get; init; }
}
