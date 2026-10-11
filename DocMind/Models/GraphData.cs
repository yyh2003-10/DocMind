using System.Text.Json.Serialization;

namespace DocMind.Models;

public record GraphNode(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("group")] string Group,
    [property: JsonPropertyName("size")] int Size,
    [property: JsonPropertyName("collection")] string Collection
);

public record GraphEdge(
    [property: JsonPropertyName("from")] string From,
    [property: JsonPropertyName("to")] string To,
    [property: JsonPropertyName("label")] string Label
);

public record GraphResponse(
    [property: JsonPropertyName("nodes")] List<GraphNode>? Nodes = null,
    [property: JsonPropertyName("edges")] List<GraphEdge>? Edges = null,
    [property: JsonPropertyName("total_nodes")] int TotalNodes = 0
)
{
    public List<GraphNode> SafeNodes => Nodes ?? new();
    public List<GraphEdge> SafeEdges => Edges ?? new();
}

public record GraphEntityRelation(
    [property: JsonPropertyName("relation_id")] int RelationId = 0,
    [property: JsonPropertyName("from_id")] string FromId = "",
    [property: JsonPropertyName("from_name")] string FromName = "",
    [property: JsonPropertyName("from_type")] string FromType = "",
    [property: JsonPropertyName("to_id")] string ToId = "",
    [property: JsonPropertyName("to_name")] string ToName = "",
    [property: JsonPropertyName("to_type")] string ToType = "",
    [property: JsonPropertyName("relation")] string Relation = ""
);

public record GraphExtractResult(
    [property: JsonPropertyName("ok")] bool Ok = false,
    [property: JsonPropertyName("extracted_count")] int ExtractedCount = 0,
    [property: JsonPropertyName("skipped_count")] int SkippedCount = 0,
    [property: JsonPropertyName("errors")] List<string>? Errors = null,
    [property: JsonPropertyName("elapsed_ms")] int ElapsedMs = 0
);

public record GraphContextSnippet(
    [property: JsonPropertyName("chunk_id")] int ChunkId = 0,
    [property: JsonPropertyName("document_id")] string DocumentId = "",
    [property: JsonPropertyName("content")] string Content = "",
    [property: JsonPropertyName("source")] string Source = "",
    [property: JsonPropertyName("heading")] string Heading = "",
    [property: JsonPropertyName("page")] int Page = 0,
    [property: JsonPropertyName("doc_title")] string DocTitle = "",
    [property: JsonPropertyName("doc_summary")] string DocSummary = ""
)
{
    [JsonPropertyName("chunk_index")]
    public int ChunkIndex { get; init; }

    [JsonPropertyName("doc_type")]
    public string DocType { get; init; } = "";

    [JsonPropertyName("evidence_reason")]
    public string EvidenceReason { get; init; } = "";

    [JsonPropertyName("sheet")]
    public string Sheet { get; init; } = "";

    [JsonPropertyName("slide")]
    public int Slide { get; init; } = 0;

    public string DisplayTitle => !string.IsNullOrWhiteSpace(DocTitle) ? DocTitle : (!string.IsNullOrWhiteSpace(Source) ? System.IO.Path.GetFileName(Source) : "未命名来源");

    public string ContentTypeLabel => DocType switch
    {
        "table" => "表格",
        "code" => "代码",
        "list" => "列表",
        "heading" => "标题",
        "paragraph" => "段落",
        _ => string.IsNullOrWhiteSpace(DocType) ? "文本" : DocType
    };

    public string ChunkPositionLabel => ChunkIndex >= 0 ? $"切片 #{ChunkIndex + 1}" : "切片位置未知";

    /// <summary>用于侧栏快速扫读的来源位置标签。</summary>
    public string LocationLabel
    {
        get
        {
            var parts = new List<string>();
            if (!string.IsNullOrWhiteSpace(Heading))
                parts.Add(Heading.Trim());
            if (Page > 0)
                parts.Add($"P.{Page}");
            if (!string.IsNullOrWhiteSpace(Sheet))
                parts.Add($"Sheet: {Sheet}");
            if (Slide > 0)
                parts.Add($"Slide: {Slide}");
            return parts.Count > 0 ? string.Join(" · ", parts) : "未标注位置";
        }
    }

    /// <summary>切片正文的短摘要，避免侧栏一打开就被长段落淹没。</summary>
    public string PreviewText
    {
        get
        {
            var text = (Content ?? string.Empty).Trim();
            return text.Length > 150 ? text[..150] + "…" : text;
        }
    }

    public bool HasSummary => !string.IsNullOrWhiteSpace(DocSummary);
    public bool HasLocation => !string.IsNullOrWhiteSpace(Heading) || Page > 0 || !string.IsNullOrWhiteSpace(Sheet) || Slide > 0;
}

public record GraphSourceDocument(
    [property: JsonPropertyName("source")] string Source = "",
    [property: JsonPropertyName("title")] string Title = "",
    [property: JsonPropertyName("summary")] string Summary = "",
    [property: JsonPropertyName("chunk_count")] int ChunkCount = 0
)
{
    public string DisplayTitle => !string.IsNullOrWhiteSpace(Title) ? Title : (!string.IsNullOrWhiteSpace(Source) ? System.IO.Path.GetFileName(Source) : "未命名文档");
}

public record GraphEntityDetailResponse(
    [property: JsonPropertyName("entity")] GraphNode? Entity = null,
    [property: JsonPropertyName("relations")] List<GraphEntityRelation>? Relations = null,
    [property: JsonPropertyName("snippets")] List<GraphContextSnippet>? Snippets = null,
    [property: JsonPropertyName("source_documents")] List<GraphSourceDocument>? SourceDocuments = null
)
{
    public List<GraphEntityRelation> SafeRelations => Relations ?? new();
    public List<GraphContextSnippet> SafeSnippets => Snippets ?? new();
    public List<GraphSourceDocument> SafeSourceDocuments => SourceDocuments ?? new();
}

public sealed record EntityDistillRequest
{
    [JsonPropertyName("entityId")]
    public string EntityId { get; init; } = string.Empty;

    [JsonPropertyName("entityName")]
    public string EntityName { get; init; } = string.Empty;

    [JsonPropertyName("entityType")]
    public string EntityType { get; init; } = "concept";

    [JsonPropertyName("collection")]
    public string? Collection { get; init; }

    [JsonPropertyName("dialogueSummary")]
    public string? DialogueSummary { get; init; }

    [property: JsonPropertyName("localSnippets")]
    public IReadOnlyList<string> LocalSnippets { get; init; } = [];

    [JsonPropertyName("webReferences")]
    public IReadOnlyList<string> WebReferences { get; init; } = [];

    [JsonPropertyName("model")]
    public string? Model { get; init; }

    [JsonPropertyName("providerConfig")]
    public ProviderConfig? ProviderConfig { get; init; }
}

public sealed record EntityDistillResponse
{
    [JsonPropertyName("entityId")]
    public string EntityId { get; init; } = string.Empty;

    [JsonPropertyName("entityName")]
    public string EntityName { get; init; } = string.Empty;

    [JsonPropertyName("markdownCard")]
    public string MarkdownCard { get; init; } = string.Empty;

    [JsonPropertyName("suggestedTags")]
    public IReadOnlyList<string> SuggestedTags { get; init; } = [];

    [JsonPropertyName("model")]
    public string Model { get; init; } = string.Empty;
}
