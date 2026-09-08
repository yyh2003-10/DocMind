namespace DocMind.Models;

/// <summary>POST /v1/llm/models 请求体 — 列出提供商可用模型（Ollama 本地 / 云端 /models）。
/// 用 UI 当前输入值拉取（无需先保存）；字段 null/空 = 沿用后端当前配置。</summary>
public sealed record LlmModelsRequest
{
    /// <summary>提供商标识（openai/ollama/anthropic/gemini）；null = 用后端当前配置。</summary>
    public string? Provider { get; init; }

    /// <summary>API Key；null/空 = 沿用后端当前配置的 key。</summary>
    public string? ApiKey { get; init; }

    /// <summary>API 基础地址；null/空 = 沿用后端当前配置。</summary>
    public string? BaseUrl { get; init; }

    /// <summary>拉取超时秒数（默认 10）。</summary>
    public double Timeout { get; init; } = 10.0;
}

/// <summary>POST /v1/llm/models 响应体。</summary>
public sealed record LlmModelsResult
{
    public bool Ok { get; init; }

    public string Provider { get; init; } = string.Empty;

    /// <summary>可用模型 ID 列表（成功时）。</summary>
    public IReadOnlyList<string> Models { get; init; } = Array.Empty<string>();

    /// <summary>模型名 → 元数据（上下文窗口等）；后端内置常见模型表补齐，未收录的模型无此键。</summary>
    public IReadOnlyDictionary<string, LlmModelMeta> ModelMeta { get; init; } = new Dictionary<string, LlmModelMeta>();

    /// <summary>失败原因（已分类：key 无效 / 服务未启动 / 接口未实现需手输等）。</summary>
    public string? Error { get; init; }
}

/// <summary>单模型元数据（上下文窗口、最大输出、是否深度推理等）。</summary>
public sealed record LlmModelMeta
{
    /// <summary>上下文窗口（token）；null = 未知。</summary>
    public int? ContextWindow { get; init; }

    /// <summary>单次最大输出上限（token）；null = 未知。</summary>
    public int? MaxOutputTokens { get; init; }

    /// <summary>是否为深度推理模型（DeepSeek-R1 / o1 / QwQ 等）。</summary>
    public bool IsReasoningModel { get; init; }

    /// <summary>友好展示名。</summary>
    public string? DisplayName { get; init; }

    /// <summary>规格摘要描述。</summary>
    public string? SummaryText { get; init; }
}

/// <summary>UI 层模型列表项：模型名 + 上下文窗口 + 规格摘要。</summary>
public sealed class LlmModelItem
{
    /// <summary>模型 ID（如 deepseek-chat、gpt-4o-mini）。</summary>
    public string Name { get; init; }

    /// <summary>上下文窗口（token）；null = 未知（前端显示"—"）。</summary>
    public int? ContextWindow { get; init; }

    /// <summary>单次最大输出上限（token）。</summary>
    public int? MaxOutputTokens { get; init; }

    /// <summary>是否为深度思考/推理模型。</summary>
    public bool IsReasoningModel { get; init; }

    /// <summary>规格摘要文本。</summary>
    public string? SummaryText { get; init; }

    public LlmModelItem(
        string name,
        int? contextWindow = null,
        int? maxOutputTokens = null,
        bool isReasoningModel = false,
        string? summaryText = null)
    {
        Name = name;
        ContextWindow = contextWindow;
        MaxOutputTokens = maxOutputTokens;
        IsReasoningModel = isReasoningModel;
        SummaryText = summaryText;
    }

    /// <summary>上下文窗口友好显示（如 "128K"、"1M"、"—"）。</summary>
    public string ContextWindowDisplay
    {
        get
        {
            if (ContextWindow is null)
            {
                return "—";
            }

            int window = ContextWindow.Value;
            return window switch
            {
                >= 1_000_000 => $"{window / 1_000_000.0:0.#}M",
                >= 1000 => $"{window / 1000.0:0.#}K",
                _ => window.ToString(),
            };
        }
    }

    /// <summary>完整规格标签（如 "上下文 128K · 最大输出 8K"）。</summary>
    public string FullSpecDisplay => !string.IsNullOrEmpty(SummaryText)
        ? SummaryText
        : $"上下文 {ContextWindowDisplay}";

    public override string ToString() => Name;
}
