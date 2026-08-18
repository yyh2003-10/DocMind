namespace DocMind.Models;

/// <summary>GET /v1/chats 列表项 — 持久化的历史会话（SQLite）。</summary>
public sealed record ChatSessionSummary
{
    public string ChatId { get; init; } = string.Empty;

    /// <summary>会话标题（首条用户问题前 50 字）。</summary>
    public string Title { get; init; } = string.Empty;

    public int MessageCount { get; init; }

    /// <summary>ISO 时间戳。</summary>
    public string CreatedAt { get; init; } = string.Empty;

    public string UpdatedAt { get; init; } = string.Empty;
}

/// <summary>GET /v1/chats 响应体（按更新时间倒序）。</summary>
public sealed record ChatSessionListResponse
{
    public IReadOnlyList<ChatSessionSummary> Chats { get; init; } = Array.Empty<ChatSessionSummary>();

    public int Total { get; init; }
}

/// <summary>会话内单条消息。</summary>
public sealed record ChatSessionMessage
{
    /// <summary>user / assistant / system。</summary>
    public string Role { get; init; } = string.Empty;

    public string Content { get; init; } = string.Empty;

    public string CreatedAt { get; init; } = string.Empty;

    public IReadOnlyList<SourceRef>? Sources { get; init; }
}

/// <summary>GET /v1/chats/{id} 响应体 — 会话全部消息（回看/续聊）。</summary>
public sealed record ChatSessionDetail
{
    public string ChatId { get; init; } = string.Empty;

    public string Title { get; init; } = string.Empty;

    public IReadOnlyList<ChatSessionMessage> Messages { get; init; } = Array.Empty<ChatSessionMessage>();
}
