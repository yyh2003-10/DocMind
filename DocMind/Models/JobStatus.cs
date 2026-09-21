namespace DocMind.Models;

using System.Text.Json;

/// <summary>对应后端 JobStatus：{job_id, type, status, progress, processed, total, started_at, finished_at, error, results, report}。</summary>
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
    /// <summary>当前正在处理的文件路径（可选）。</summary>
    public string? CurrentFile { get; init; }
    /// <summary>当前文件内阶段（可选）：parsing/chunking/embedding/writing/curating。旧后端为 null。</summary>
    public string? Stage { get; init; }
    /// <summary>阶段内进度 0.0~1.0（可选，embedding 阶段有真实值，其余为 null）。</summary>
    public double? StageProgress { get; init; }
    /// <summary>异步 job 完成后的详细结果列表（可选，向后兼容），由后端在完成时填充。</summary>
    public IReadOnlyList<IngestResult> Results { get; init; } = [];
    /// <summary>curate 任务完成后的整理报告（其它类型任务为 null）。原始 JSON 保留结构，UI 按需解析。</summary>
    public JsonElement? Report { get; init; }
    /// <summary>FC-01b：取消时后端人话说明（已导入 N 篇…）；完成任务为 null。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("cancel_note")]
    public string? CancelNote { get; init; }
}
