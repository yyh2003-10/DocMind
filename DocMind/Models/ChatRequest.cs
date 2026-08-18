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

    /// <summary>引用 chunk 数；null = 用后端配置的 rag_top_k（设置页「RAG Top-K」），
    /// 避免对话页硬编码默认值覆盖用户配置。</summary>
    public int? TopK { get; init; }

    /// <summary>会话 ID（多轮对话传同一值，实现追问上下文）。</summary>
    public string? ChatId { get; init; }

    /// <summary>按请求覆盖模型名（对话页快速切换模型）；null = 用设置页配置的 llm_model。</summary>
    public string? Model { get; init; }

    /// <summary>是否开启 AI 实时联网搜索拓宽知识来向。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("enableWebSearch")]
    public bool EnableWebSearch { get; init; }

    /// <summary>知识图谱实体上下文（High-level 拓扑与背景注入）。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("entityContext")]
    public string? EntityContext { get; init; }

    /// <summary>办公角色人设标识（office/architect/engineer/brainstorm）。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("persona")]
    public string? Persona { get; init; }
}