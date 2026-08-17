using System.Text.Json.Serialization;

namespace DocMind.Models;

/// <summary>后端 /v1/events SSE 推送的事件消息。</summary>
public record EventMessage(
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("ts")] string? Ts = null,
    [property: JsonPropertyName("path")] string? Path = null,
    [property: JsonPropertyName("collection")] string? Collection = null,
    [property: JsonPropertyName("result")] string? Result = null,
    [property: JsonPropertyName("document_id")] string? DocumentId = null,
    [property: JsonPropertyName("error")] string? Error = null
);
