namespace DocMind.Models;

/// <summary>对应后端 IngestResultDTO。</summary>
public sealed record IngestResult
{
    public string Source { get; init; } = string.Empty;
    public string Collection { get; init; } = string.Empty;
    public string Format { get; init; } = string.Empty;
    public long SizeBytes { get; init; }
    public int ChunkCount { get; init; }
    public int ElapsedMs { get; init; }
    public string Status { get; init; } = string.Empty;
    public string? Error { get; init; }
    public string? DocumentId { get; init; }

    /// <summary>后端 AI 自动整理结果（enrich/categorize）：含 tags、summary 等；
    /// 未触发自动整理时为 null。</summary>
    public IReadOnlyDictionary<string, object>? Curation { get; init; }

    // ── Curation 显示辅助属性 ──

    /// <summary>AI 自动生成的标签列表（如 #技术 #架构）。</summary>
    public IReadOnlyList<string> CurationTags =>
        Curation?.TryGetValue("tags", out var t) == true && t is System.Collections.IEnumerable tags
            ? tags.Cast<object>().Where(o => o != null).Select(o => $"#{o}").ToList()
            : [];

    /// <summary>AI 自动生成的摘要（前 120 字符）。</summary>
    public string CurationSummary =>
        Curation?.TryGetValue("summary", out var s) == true && s is string summary
            ? (summary.Length > 120 ? summary[..120] + "…" : summary)
            : string.Empty;

    /// <summary>AI 自动归类的目标集合名。</summary>
    public string CurationCollection =>
        Curation?.TryGetValue("collection", out var c) == true && c is string col
            ? col
            : string.Empty;

    /// <summary>是否有任何 AI 整理结果可展示。</summary>
    public bool HasCuration => Curation is { Count: > 0 };
}

public sealed record IngestResponse
{
    public IReadOnlyList<IngestResult> Ingested { get; init; } = [];
    /// <summary>后端 skipped 是 int（跳过数），不是文件名列表。</summary>
    public int Skipped { get; init; }
    /// <summary>后端 failed 是 int（失败数），不是文件名列表。</summary>
    public int Failed { get; init; }
    /// <summary>失败明细：每个失败文件的 source + error（后端 status="failed" 的条目）。</summary>
    public IReadOnlyList<IngestResult> FailedDetails { get; init; } = [];
    public int TotalDocuments { get; init; }
    public int TotalChunks { get; init; }
}
