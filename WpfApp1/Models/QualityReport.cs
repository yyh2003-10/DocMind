namespace DocMind.Models;

public sealed record QualityReport
{
    public string? Collection { get; init; }
    public double? DuplicateRate { get; init; }
    public double? MissingMetadataRate { get; init; }
    public int TotalDocuments { get; init; }
    public int TotalChunks { get; init; }
    public IReadOnlyList<string> Warnings { get; init; } = [];
}
