using System.Text.Json.Serialization;

namespace DocMind.Models;

/// <summary>
/// 自定义 AI 服务商：一份可复用的命名配置（名称 + 提供商 + BaseURL + API Key +
/// 该服务商全部可用模型列表 + 默认模型 + 温度/Token 上限）。
/// 保存后即出现在设置页/对话页的服务商下拉框中，可一键应用与切换；
/// 使用模型场景中可自由选择该服务商的任意模型，也可指定默认模型。
/// </summary>
public sealed class LlmProfile
{
    /// <summary>稳定标识（GUID）；删除/更新不依赖显示名。</summary>
    public string Id { get; set; } = Guid.NewGuid().ToString("N");

    /// <summary>服务商显示名（如 "DeepSeek 工作机"、"本地 Ollama"、"Claude 官方"）。</summary>
    public string Name { get; set; } = "";

    /// <summary>提供商标识（none | openai | ollama | anthropic | gemini）。</summary>
    public string Provider { get; set; } = "openai";

    /// <summary>API 基础地址；空 = 用提供商官方默认。</summary>
    public string? BaseUrl { get; set; }

    /// <summary>默认模型名；空 = 未指定（使用后端当前配置的模型）。</summary>
    public string? Model { get; set; }

    /// <summary>该服务商的全部可用模型列表（对话页模型下拉候选；可手动维护或从服务商拉取）。</summary>
    public List<string> Models { get; set; } = new();

    /// <summary>模型名 → 上下文窗口（token）；保存服务商时由「获取模型」拉取的元数据落盘。
    /// 未收录的模型无此键（前端显示"—"）。加载档案时据此恢复上下文长度展示。</summary>
    public Dictionary<string, int> ModelContextWindows { get; set; } = new();

    /// <summary>API Key。内存/运行时持明文；落盘时由 AppSettings.Save 经 DPAPI 加密。</summary>
    public string? ApiKey { get; set; }

    /// <summary>温度覆盖；null = 用设置页默认值。</summary>
    public double? Temperature { get; set; }

    /// <summary>最大 token 覆盖；null = 用设置页默认值。</summary>
    public int? MaxTokens { get; set; }

    /// <summary>备注（可选）。</summary>
    public string? Description { get; set; }

    /// <summary>启用开关：停用后不出现在对话页模型点选列表（配置保留，可随时启用）。</summary>
    public bool IsEnabled { get; set; } = true;

    /// <summary>载入时该服务商的 ApiKey 密文解密失败（换 Windows 用户/文件损坏）。
    /// 仅运行时标志：应用时提示重输，不落盘。</summary>
    [JsonIgnore]
    public bool KeyDecryptFailed { get; set; }

    /// <summary>深拷贝（应用服务商到表单/单例时避免引用共享导致互相污染）。</summary>
    public LlmProfile Clone() => new()
    {
        Id = Id,
        Name = Name,
        Provider = Provider,
        BaseUrl = BaseUrl,
        Model = Model,
        Models = Models?.ToList() ?? new List<string>(),
        ModelContextWindows = ModelContextWindows?.ToDictionary(kv => kv.Key, kv => kv.Value) ?? new(),
        ApiKey = ApiKey,
        Temperature = Temperature,
        MaxTokens = MaxTokens,
        Description = Description,
        IsEnabled = IsEnabled,
        KeyDecryptFailed = KeyDecryptFailed,
    };

    public override string ToString() => Name;
}
