namespace DocMind.Models;

public sealed record SearchResponse
{
    public string Query { get; init; } = string.Empty;
    public IReadOnlyList<SearchHit> Hits { get; init; } = [];
    public int Total { get; init; }
    public double ElapsedMs { get; init; }
}
