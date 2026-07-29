namespace DocMind.Models;

public sealed record ApiError
{
    public string Code { get; init; } = string.Empty;
    public string Message { get; init; } = string.Empty;
    public string? Detail { get; init; }
}
