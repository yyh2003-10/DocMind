namespace DocMind.Models;

/// <summary>对应后端 DocumentDTO。</summary>
public sealed record Document
{
    public string Id { get; init; } = string.Empty;
    /// <summary>后端字段叫 source（原文件名），不是 name。</summary>
    public string Source { get; init; } = string.Empty;
    public string Collection { get; init; } = string.Empty;
    public string Format { get; init; } = string.Empty;
    public string FileHash { get; init; } = string.Empty;
    public long SizeBytes { get; init; }
    public int? PageCount { get; init; }
    public int ChunkCount { get; init; }
    public string CreatedAt { get; init; } = string.Empty;
    public string UpdatedAt { get; init; } = string.Empty;
}
