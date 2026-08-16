namespace DocMind.Models;

/// <summary>对应后端文档详情接口 chunks_preview 数组项：
/// {chunk_id, chunk_index, content, tokens, doc_type, page, heading}。</summary>
public sealed record Chunk
{
    public int ChunkId { get; init; }
    public int ChunkIndex { get; init; }
    public string Content { get; init; } = string.Empty;
    public int Tokens { get; init; }
    public string? DocType { get; init; }
    public int? Page { get; init; }
    public string? Heading { get; init; }
}
