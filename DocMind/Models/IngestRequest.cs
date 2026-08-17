namespace DocMind.Models;

public sealed record IngestRequest
{
    public string Path { get; init; } = string.Empty;
    public string? Collection { get; init; }
    public bool Recursive { get; init; }
    /// <summary>强制重新摄入已存在的文件（覆盖）。</summary>
    public bool Force { get; init; }
}

public sealed record IngestTextRequest
{
    [System.Text.Json.Serialization.JsonPropertyName("text")]
    public string Text { get; init; } = string.Empty;

    [System.Text.Json.Serialization.JsonPropertyName("title")]
    public string? Title { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("collection")]
    public string? Collection { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("force")]
    public bool Force { get; init; } = true;
}
