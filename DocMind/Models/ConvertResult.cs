namespace DocMind.Models;

/// <summary>对应后端 ConvertResponse：{input, output_format, content, elements_count}。</summary>
public sealed record ConvertResult
{
    public string Input { get; init; } = string.Empty;
    public string OutputFormat { get; init; } = string.Empty;
    public string Content { get; init; } = string.Empty;
    public int ElementsCount { get; init; }

    /// <summary>UI 兼容：转换成功当 Content 非空时为 true。</summary>
    public bool Success => !string.IsNullOrWhiteSpace(Content);
    /// <summary>UI 兼容：预览文本即 Content。</summary>
    public string? Message => Content;
    /// <summary>UI 兼容：后端在指定 output_path 时落盘，此处不返回路径（由 VM 用 OutputPath 提示）。</summary>
    public string? OutputPath => null;
    /// <summary>UI 兼容：保留 Format 别名指向 OutputFormat。</summary>
    public string? Format => OutputFormat;
}
