namespace DocMind.Models;

public sealed record SearchRequest
{
    public string Query { get; init; } = string.Empty;
    public string? Collection { get; init; }
    public int TopK { get; init; } = 10;
    public double? MinScore { get; init; }

    /// <summary>按集合之外的维度过滤结果（后端 filter 参数）；null = 不过滤。</summary>
    public IReadOnlyDictionary<string, string>? Filter { get; init; }

    /// <summary>是否在搜索结果中高亮匹配关键词（后端 highlight 参数）。</summary>
    public bool Highlight { get; init; }
}
