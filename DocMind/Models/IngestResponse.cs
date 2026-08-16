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
