using System.Text.Json.Serialization;

namespace DocMind.Models;

/// <summary>回收站条目（GET /v1/trash）。</summary>
public sealed record TrashItem
{
    [JsonPropertyName("document_id")]
    public string DocumentId { get; init; } = "";
    public string Source { get; init; } = "";
    public string Collection { get; init; } = "default";
    [JsonPropertyName("deleted_at")]
    public string DeletedAt { get; init; } = "";
    [JsonPropertyName("purged_at")]
    public string? PurgedAt { get; init; }

    /// <summary>展示用短文件名。</summary>
    public string DisplaySource
    {
        get
        {
            if (string.IsNullOrWhiteSpace(Source)) return DocumentId;
            try
            {
                var name = System.IO.Path.GetFileName(Source.TrimEnd('\\', '/'));
                return string.IsNullOrWhiteSpace(name) ? Source : name;
            }
            catch { return Source; }
        }
    }
}

/// <summary>GET /v1/trash 响应。</summary>
public sealed record TrashListResponse
{
    public List<TrashItem> Items { get; init; } = new();
    public int Total { get; init; }
}

/// <summary>POST /v1/trash/{id}/restore 响应。</summary>
public sealed record TrashRestoreResponse
{
    public string Id { get; init; } = "";
    public string Status { get; init; } = "";
    /// <summary>后端提示：恢复后需重新摄入/reindex 才能被检索命中。</summary>
    public string Note { get; init; } = "";
}

/// <summary>POST /v1/trash/purge 请求与响应。</summary>
public sealed record TrashPurgeRequest
{
    [JsonPropertyName("older_than_days")]
    public int OlderThanDays { get; init; } = 30;
}

public sealed record TrashPurgeResponse
{
    public int Purged { get; init; }
}

/// <summary>设置页「当前生效配置」列表项（FC-04）。</summary>
public sealed record EffectiveConfigItem
{
    public string Category { get; init; } = "";
    public string Name { get; init; } = "";
    public string Value { get; init; } = "";
    /// <summary>生效语义提示：实时 / 重启后端后生效 / 下次启动等。</summary>
    public string EffectHint { get; init; } = "";
}
