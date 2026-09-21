namespace DocMind.Models;

/// <summary>对应后端 GET /v1/library/status。</summary>
public sealed record LibraryStatus
{
    /// <summary>ok | empty | warn | reindex_needed</summary>
    public string Status { get; init; } = "ok";
    public int TotalDocuments { get; init; }
    public int TotalChunks { get; init; }
    public string EmbedModel { get; init; } = string.Empty;
    public int? EmbedDim { get; init; }
    public int? StoreDim { get; init; }
    public int EmbedMaxLength { get; init; }
    public int ChunkMaxTokens { get; init; }
    public bool Aligned { get; init; } = true;
    public IReadOnlyList<LibraryIssue> Issues { get; init; } = [];
    public string Summary { get; init; } = string.Empty;

    public bool NeedsReindex => Status == "reindex_needed";
    public bool IsEmpty => Status == "empty";
    public bool HasWarn => Status == "warn";
    public bool IsOk => Status == "ok";
}

public sealed record LibraryIssue
{
    public string Code { get; init; } = string.Empty;
    /// <summary>info | warn | error</summary>
    public string Level { get; init; } = "info";
    public string Message { get; init; } = string.Empty;
    /// <summary>ingest | reindex | align_config</summary>
    public string Action { get; init; } = string.Empty;
}

/// <summary>POST /v1/profile 响应。</summary>
public sealed record ProfileSwitchResult
{
    public string Profile { get; init; } = string.Empty;
    public ProfilePreset? Preset { get; init; }
    public ProfileApplied? Applied { get; init; }
}

public sealed record ProfilePreset
{
    public string Label { get; init; } = string.Empty;
    public string Description { get; init; } = string.Empty;
}

public sealed record ProfileApplied
{
    public int RagTopK { get; init; }
    public double RagMinScore { get; init; }
    public string RagMode { get; init; } = string.Empty;
    public string QueryExpansion { get; init; } = string.Empty;
}
