namespace DocMind.Models;

public sealed record ConvertRequest
{
    public string InputPath { get; init; } = string.Empty;
    /// <summary>后端 output_path: str | None；空时发 null（不落盘，仅预览）。</summary>
    public string? OutputPath { get; init; }
    public string? Format { get; init; }
    public string? Collection { get; init; }
}
