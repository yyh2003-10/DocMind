namespace DocMind.Models;

public sealed record JobStatus
{
    public string JobId { get; init; } = string.Empty;
    public string Status { get; init; } = string.Empty;
    public int? Progress { get; init; }
    public string? Message { get; init; }
    public DateTimeOffset? StartedAt { get; init; }
    public DateTimeOffset? UpdatedAt { get; init; }
}
