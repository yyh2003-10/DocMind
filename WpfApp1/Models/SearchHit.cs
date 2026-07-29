namespace DocMind.Models;

public sealed record SearchHit
{
    public Chunk Chunk { get; init; } = new();
    public double Score { get; init; }
}
