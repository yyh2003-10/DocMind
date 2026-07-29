namespace DocMind.Models;

public sealed record Document
{
    public string Id { get; init; } = string.Empty;
    public string Name { get; init; } = string.Empty;
    public string? Path { get; init; }
    public string? Collection { get; init; }
    public string? Format { get; init; }
    public long SizeBytes { get; init; }
    public int ChunkCount { get; init; }
    public DateTimeOffset CreatedAt { get; init; }
    public DateTimeOffset? UpdatedAt { get; init; }
}
