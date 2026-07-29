namespace DocMind.Models;

public sealed record HealthStatus
{
    public string Status { get; init; } = string.Empty;
    public string? Version { get; init; }
    public double? UptimeSeconds { get; init; }
    public DateTimeOffset? Timestamp { get; init; }
}
