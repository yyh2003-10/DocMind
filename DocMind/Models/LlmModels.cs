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

    /// <summary>失败原因（已分类：key 无效 / 服务未启动 / 接口未实现需手输等）。</summary>
    public string? Error { get; init; }
}
