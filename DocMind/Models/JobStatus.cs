namespace DocMind.Models;

/// <summary>对应后端 JobStatus：{job_id, type, status, progress, processed, total, started_at, finished_at, error, results}。</summary>
public sealed record JobStatus
{
    public string JobId { get; init; } = string.Empty;
    public string Type { get; init; } = string.Empty;
    public string Status { get; init; } = string.Empty;
    /// <summary>进度 0.0 ~ 1.0。</summary>
    public double Progress { get; init; }
    public int Processed { get; init; }
    public int Total { get; init; }
    public string? StartedAt { get; init; }
    public string? FinishedAt { get; init; }
    public string? Error { get; init; }
    /// <summary>异步 job 完成后的详细结果列表（可选，向后兼容），由后端在完成时填充。</summary>
    public IReadOnlyList<IngestResult> Results { get; init; } = [];
}
