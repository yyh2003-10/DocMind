namespace DocMind.Helpers;

/// <summary>
/// LLM 服务地址归一化：Trim、去末尾斜杠、补协议头、按 provider 纠正 /v1 后缀。
/// 本地运行时（LM Studio/Ollama）按根地址存储（与其服务界面展示一致，
/// 如 http://127.0.0.1:1234）；OpenAI 兼容通道的 /v1 由后端 factory 对
/// 本地无路径地址自动补全，用户无需关心 /v1。
/// </summary>
public static class LlmUrlNormalizer
{
    /// <summary>归一化服务地址；输入为空或无有效主机时返回 null。</summary>
    public static string? Normalize(string? url, string? provider)
    {
        if (string.IsNullOrWhiteSpace(url)) return null;

        var u = url.Trim().TrimEnd('/');
        // 只有协议头没有主机（如用户误输入 "http://"）视为无效
        if (u is "" or "http:" or "https:" or "http" or "https") return null;

        if (!u.Contains("://"))
        {
            u = "http://" + u;
        }

        // Ollama 原生通道不认 /v1（用户误带 OpenAI 兼容后缀会导致 /v1/api/chat 404）
        if (string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase)
            && u.EndsWith("/v1", StringComparison.OrdinalIgnoreCase))
        {
            u = u[..^3];
        }

        return u;
    }
}
