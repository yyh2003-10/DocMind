namespace DocMind.Models;

/// <summary>对应后端 StatsResponse：collections 是 dict[str, list[int]]，
/// 即 {collection_name: [doc_count, chunk_count, size_bytes]}。</summary>
public sealed record Stats
{
    public int TotalDocuments { get; init; }
    public int TotalChunks { get; init; }
    /// <summary>后端字段类型是 dict[str, list[int]]，序列化到 JSON 后是
    /// {collection_name: [doc_count, chunk_count, size_bytes]}。用 Dictionary 解析。</summary>
    public IReadOnlyDictionary<string, int[]> Collections { get; init; } = new Dictionary<string, int[]>();
}

public sealed record CollectionStats
{
    public string Name { get; init; } = string.Empty;
    public int Documents { get; init; }
    public int Chunks { get; init; }
    public long SizeBytes { get; init; }
}
