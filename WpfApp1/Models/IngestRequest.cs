namespace DocMind.Models;

public sealed record IngestRequest
{
    public string Path { get; init; } = string.Empty;
    public string? Collection { get; init; }
    public bool Recursive { get; init; }
    /// <summary>强制重新摄入已存在的文件（覆盖）。</summary>
    public bool Force { get; init; }
}
