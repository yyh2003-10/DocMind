namespace DocMind.Models;

public sealed record ReindexRequest
{
    public string? Collection { get; init; }
    public bool Full { get; init; }
}
