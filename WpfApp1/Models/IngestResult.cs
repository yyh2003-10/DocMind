namespace DocMind.Models;

public sealed record IngestResult
{
    public Document Document { get; init; } = new();
    public int ChunksAdded { get; init; }
    public int DuplicatesSkipped { get; init; }
    public string Status { get; init; } = string.Empty;
}
