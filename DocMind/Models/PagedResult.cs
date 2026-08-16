namespace DocMind.Models;

/// <summary>对应后端 ListDocumentsResponse：{documents, total, page, page_size}。</summary>
public sealed record DocumentListResponse
{
    public IReadOnlyList<Document> Documents { get; init; } = [];
    public int Total { get; init; }
    public int Page { get; init; }
    public int PageSize { get; init; }
}
