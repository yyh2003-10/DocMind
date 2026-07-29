namespace DocMind.Models;

public sealed record ConvertResult
{
    public bool Success { get; init; }
    public string? InputPath { get; init; }
    public string? OutputPath { get; init; }
    public string? Format { get; init; }
    public string? Message { get; init; }
}
