namespace DocMind.Models;

public sealed record CollectionStats
{
    public string Name { get; init; } = string.Empty;
    public int Documents { get; init; }
    public int Chunks { get; init; }
    public long SizeBytes { get; init; }
}
