namespace DocMind.Models;

public sealed record IngestRequest
{
    public string Path { get; init; } = string.Empty;
    public string? Collection { get; init; }
    public bool Recursive { get; init; }
}
