namespace DocMind.Models;

public sealed record Stats
{
    public int TotalDocuments { get; init; }
    public int TotalChunks { get; init; }
    public int TotalCollections { get; init; }
    public IReadOnlyList<CollectionStats> Collections { get; init; } = [];
    public DateTimeOffset? GeneratedAt { get; init; }
}
