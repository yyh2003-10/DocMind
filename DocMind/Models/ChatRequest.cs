namespace DocMind.Models;

/// <summary>按请求携带的服务商配置（对应后端 ProviderConfigIn）。
/// 对话页「点选模型即切服务商」时随 /v1/chat 请求传入，按请求生效、不污染后端全局配置；
/// 全部字段为 null/空时后端忽略，等同使用全局配置。</summary>
public sealed record ProviderConfig
{
    /// <summary>协议类型（openai/anthropic/gemini/ollama）；null = 沿用后端全局 provider。</summary>
    public string? Provider { get; init; }

    /// <summary>API Key；null/空 = 沿用后端当前配置的 key。</summary>
    public string? ApiKey { get; init; }

    /// <summary>API 基础地址；null/空 = 沿用后端当前配置。</summary>
    public string? BaseUrl { get; init; }

    /// <summary>模型名；null/空 = 沿用后端当前配置。</summary>
    public string? Model { get; init; }

    /// <summary>温度覆盖；null = 沿用后端配置。</summary>
    public double? Temperature { get; init; }

    /// <summary>最大 token 覆盖；null = 沿用后端配置。</summary>
    public int? MaxTokens { get; init; }
}

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

    /// <summary>按请求携带的服务商配置（对话页点选模型即切服务商）；null = 用后端全局配置。
    /// 多服务商并存时按请求生效，不修改后端全局配置。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("providerConfig")]
    public ProviderConfig? ProviderConfig { get; init; }

    /// <summary>是否开启 AI 实时联网搜索拓宽知识来向。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("enableWebSearch")]
    public bool EnableWebSearch { get; init; }

    /// <summary>联网搜索模式："normal"（普通搜索）| "deep"（深度搜索）；null/空 = normal。
    /// 仅在 EnableWebSearch=true 时有意义。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("webSearchMode")]
    public string? WebSearchMode { get; init; }

    /// <summary>知识图谱实体上下文（High-level 拓扑与背景注入）。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("entityContext")]
    public string? EntityContext { get; init; }

    /// <summary>办公角色人设标识（office/architect/engineer/brainstorm 或 custom_xxx）。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("persona")]
    public string? Persona { get; init; }

    /// <summary>自定义角色系统提示词（persona 为 custom_* 时随请求携带，后端据此生效）。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("personaPrompt")]
    public string? PersonaPrompt { get; init; }

    /// <summary>本次对话临时附带的本地文档或图片路径列表（由后端工具实时提取与OCR）。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("attachments")]
    public IReadOnlyList<string>? Attachments { get; init; }

    /// <summary>联网搜索 GitHub 通道用的个人令牌（每个用户填自己的，随请求携带；
    /// 留空 = 用 GitHub 公开额度 10 次/分钟）。绝不写入后端全局配置，避免多用户共享同一账户。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("githubToken")]
    public string? GithubToken { get; init; }

    /// <summary>RAG 问答模式（"strict" / "hybrid"）；null = 沿用后端全局配置。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("ragMode")]
    public string? RagMode { get; init; }

    /// <summary>用户记忆上下文：独立字段，由后端注入生成消息；
    /// 绝不拼进 Query（避免污染检索与会话历史）。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("memoryContext")]
    public string? MemoryContext { get; init; }
}