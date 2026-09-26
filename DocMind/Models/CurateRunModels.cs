namespace DocMind.Models;

/// <summary>GET /v1/curate-runs 单条整理运行记录。</summary>
public sealed record CurateRunItem
{
    public int Id { get; init; }
    public string StartedAt { get; init; } = "";
    public string FinishedAt { get; init; } = "";
    public bool DryRun { get; init; }
    public string? Collection { get; init; }
    public List<string> Actions { get; init; } = new();
    public List<string> ChangedDocIds { get; init; } = new();
    public int SkippedCount { get; init; }
    public int ErrorCount { get; init; }
    public int ElapsedMs { get; init; }
    public string? Note { get; init; }
}

/// <summary>GET /v1/curate-runs 响应。</summary>
public sealed record CurateRunsResponse
{
    public List<CurateRunItem> Items { get; init; } = new();
    public int Total { get; init; }
}

/// <summary>POST /v1/eval/library 本库检索自评估响应（宽松字段，未知键忽略）。</summary>
public sealed record LibraryEvalResult
{
    public double? SelfRecallAtK { get; init; }
    public double? Mrr { get; init; }
    public int Sample { get; init; }
    public List<string> Suggestions { get; init; } = new();
    public string? Summary { get; init; }
    /// <summary>原始 JSON 片段，便于 UI 展示未知扩展字段。</summary>
    public string RawJson { get; init; } = "";
}

/// <summary>GET /v1/config/retrieval-recommended 预览/应用响应（宽松）。</summary>
public sealed record RetrievalRecommendedResult
{
    public bool AlignedBefore { get; init; }
    public bool Applied { get; init; }
    public string Description { get; init; } = "";
    public Dictionary<string, string> Changes { get; init; } = new();
    public string RawJson { get; init; } = "";
}
