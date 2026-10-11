namespace DocMind.Models;

/// <summary>对话页/图谱页联网搜索模式选项（UI 绑定用）。</summary>
public sealed record WebSearchModeChoice(string Key, string Label)
{
    public static readonly WebSearchModeChoice Off = new("off", "关闭");
    public static readonly WebSearchModeChoice Normal = new("normal", "普通搜索");
    public static readonly WebSearchModeChoice Deep = new("deep", "深度搜索");

    public static IReadOnlyList<WebSearchModeChoice> All { get; } = new[]
    {
        Off, Normal, Deep,
    };

    public static WebSearchModeChoice FromKey(string? key) =>
        (key ?? "").Trim().ToLowerInvariant() switch
        {
            "deep" => Deep,
            "normal" => Normal,
            "off" => Off,
            _ => Off,
        };
}
