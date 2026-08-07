namespace DocMind.Models;

/// <summary>对应后端 DeleteResponse：{id, deleted_chunks, status}。</summary>
public sealed record DeleteResult
{
    public string Id { get; init; } = string.Empty;
    /// <summary>删除的 chunk 数。</summary>
    public int DeletedChunks { get; init; }
    /// <summary>后端 status：deleted / not_found。</summary>
    public string Status { get; init; } = string.Empty;

    public bool Deleted => Status.Equals("deleted", StringComparison.OrdinalIgnoreCase);
}
