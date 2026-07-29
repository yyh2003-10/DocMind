namespace DocMind.Models;

public sealed record DeleteResult
{
    public string Id { get; init; } = string.Empty;
    public bool Deleted { get; init; }
    public string? Message { get; init; }
}
