namespace DocMind.Models;

/// <summary>对应后端 QualityResponse。</summary>
public sealed record QualityReport
{
    public string? Collection { get; init; }
    public int TotalDocuments { get; init; }
    public int TotalChunks { get; init; }
    /// <summary>后端字段名 format_distribution。</summary>
    public IReadOnlyDictionary<string, int> FormatDistribution { get; init; } = new Dictionary<string, int>();
    /// <summary>后端 warnings：质量告警清单（如分块数为 0、体积过大）。</summary>
    public IReadOnlyList<string> Warnings { get; init; } = [];
}
