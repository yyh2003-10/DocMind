namespace DocMind.Models;

public sealed record ConvertRequest
{
    public string InputPath { get; init; } = string.Empty;
    public string OutputPath { get; init; } = string.Empty;
    public string? Format { get; init; }
    public string? Collection { get; init; }
}
