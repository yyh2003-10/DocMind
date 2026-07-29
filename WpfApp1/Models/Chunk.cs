namespace DocMind.Models;

public sealed record Chunk
{
    public string Id { get; init; } = string.Empty;
    public string DocumentId { get; init; } = string.Empty;
    public int Index { get; init; }
    public string Content { get; init; } = string.Empty;
    public double? Score { get; init; }
    public DateTimeOffset? CreatedAt { get; init; }
}
