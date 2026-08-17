namespace DocMind.Models;

/// <summary>对应后端 SourceRefDTO（RAG 回答引用来源）。</summary>
public sealed record SourceRef
{
    public int Index { get; init; }
    public string Source { get; init; } = string.Empty;
    /// <summary>后端分块 ID（可空，兼容旧会话数据）。用于定位到原文档中的精确分块。</summary>
    public int? ChunkId { get; init; }
    public string Format { get; init; } = string.Empty;
    public int? Page { get; init; }
    public string? Heading { get; init; }
    public double Score { get; init; }
    public string SourceType { get; init; } = "local";
    public string? Url { get; init; }
    public string? Title { get; init; }

    public bool IsWebSource => SourceType == "web" || !string.IsNullOrWhiteSpace(Url);

    public string DisplayTitle => !string.IsNullOrWhiteSpace(Title) ? Title : Source;
}

/// <summary>POST /v1/chat 响应体。</summary>
public sealed record ChatResponse
{
    /// <summary>LLM 生成的回答文本。</summary>
    public string Answer { get; init; } = string.Empty;

    /// <summary>会话 ID（多轮对话时传同一值）。</summary>
    public string ChatId { get; init; } = string.Empty;

    /// <summary>使用的模型名。</summary>
    public string Model { get; init; } = string.Empty;

    /// <summary>提供商标识（openai / ollama）。</summary>
    public string Provider { get; init; } = string.Empty;

    /// <summary>引用 chunk 总数。</summary>
    public int TotalChunks { get; init; }

    /// <summary>耗时（毫秒）。</summary>
    public int ElapsedMs { get; init; }

    /// <summary>引用来源列表。</summary>
    public IReadOnlyList<SourceRef> Sources { get; init; } = [];
}

/// <summary>POST /v1/chat/stream 终帧（done=true）解析结果。</summary>
public sealed record ChatStreamResult
{
    public string ChatId { get; init; } = string.Empty;
    public string Model { get; init; } = string.Empty;
    public string Provider { get; init; } = string.Empty;
    public int TotalChunks { get; init; }
    public int ElapsedMs { get; init; }
    public IReadOnlyList<SourceRef> Sources { get; init; } = [];
}