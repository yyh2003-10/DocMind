using System.Text.Json.Serialization;

namespace DocMind.Models;

/// <summary>知识图谱规模统计（GET /v1/graph/stats）。</summary>
public sealed record GraphStats
{
    [property: JsonPropertyName("entity_count")] public int EntityCount { get; init; }
    [property: JsonPropertyName("relation_count")] public int RelationCount { get; init; }
    [property: JsonPropertyName("collection")] public string? Collection { get; init; }
}