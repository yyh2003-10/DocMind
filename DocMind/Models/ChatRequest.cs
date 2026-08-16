namespace DocMind.Models;

/// <summary>POST /v1/chat 请求体。</summary>
public sealed record ChatRequest
{
    /// <summary>用户问题。</summary>
    public string Query { get; init; } = string.Empty;

    /// <summary>检索集合名，null/空 = 默认集合（单集合兼容字段）。</summary>
    public string? Collection { get; init; }

    /// <summary>检索集合名列表（多选知识库）。为空或 null 时回退到 Collection / 默认集合。</summary>
    public IReadOnlyList<string>? Collections { get; init; }

    /// <summary>引用 chunk 数。</summary>
    public int TopK { get; init; } = 5;

    /// <summary>会话 ID（多轮对话传同一值，实现追问上下文）。</summary>
    public string? ChatId { get; init; }
}