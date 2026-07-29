namespace DocMind.Models;

public sealed record IngestResponse
{
    public IReadOnlyList<IngestResult> Ingested { get; init; } = [];
    public IReadOnlyList<string> Skipped { get; init; } = [];
    public IReadOnlyList<string> Failed { get; init; } = [];
    public int TotalDocuments { get; init; }
}
