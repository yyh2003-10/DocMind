namespace DocMind.Models;

/// <summary>POST /v1/llm/test 请求体 — 用 UI 当前输入值测试 LLM 连接（无需先保存）。</summary>
public sealed record LlmTestRequest
{
    /// <summary>提供商标识（none/openai/ollama/anthropic/gemini）；null = 用后端当前配置。</summary>
    public string? Provider { get; init; }

    /// <summary>API Key；null/空 = 沿用后端当前配置的 key（PasswordBox 留空 = 不修改）。</summary>
    public string? ApiKey { get; init; }

    /// <summary>API 基础地址；null/空 = 沿用后端当前配置。</summary>
    public string? BaseUrl { get; init; }

    /// <summary>模型名；null/空 = 沿用后端当前配置。</summary>
    public string? Model { get; init; }

    /// <summary>测试超时秒数（默认 15）。</summary>
    public double Timeout { get; init; } = 15.0;
}

/// <summary>POST /v1/llm/test 响应体。</summary>
public sealed record LlmTestResult
{
    public bool Ok { get; init; }

    public string Provider { get; init; } = string.Empty;

    public string Model { get; init; } = string.Empty;

    /// <summary>回复预览（成功时）。</summary>
    public string? ReplyPreview { get; init; }

    public int ElapsedMs { get; init; }

    /// <summary>失败原因（已分类：key 无效 / 地址错误 / 网络不通 / 运行库缺失 / 超时）。</summary>
    public string? Error { get; init; }
}
