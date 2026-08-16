namespace DocMind.Models;

/// <summary>对应后端 GET /v1/documents/{id}：{document, chunks_preview}。</summary>
public sealed record DocumentDetail
{
    public Document Document { get; init; } = new();
    public IReadOnlyList<Chunk> ChunksPreview { get; init; } = [];
}
