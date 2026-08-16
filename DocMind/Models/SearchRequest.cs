namespace DocMind.Models;

public sealed record SearchRequest
{
    public string Query { get; init; } = string.Empty;
    public string? Collection { get; init; }
    public int TopK { get; init; } = 10;
    public double? MinScore { get; init; }
}
