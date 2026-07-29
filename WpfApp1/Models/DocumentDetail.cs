namespace DocMind.Models;

public sealed record DocumentDetail
{
    public Document Document { get; init; } = new();
    public IReadOnlyList<Chunk> Chunks { get; init; } = [];
}
