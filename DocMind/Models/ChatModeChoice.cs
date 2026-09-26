namespace DocMind.Models;

/// <summary>对话回答模式候选：自动 / 知识库 RAG / Agent 工具循环。</summary>
public sealed record ChatModeChoice(string Key, string Label)
{
    public static readonly ChatModeChoice Auto = new("auto", "自动");
    public static readonly ChatModeChoice Rag = new("rag", "知识库 RAG");
    public static readonly ChatModeChoice Agent = new("agent", "Agent 工具");

    public static IReadOnlyList<ChatModeChoice> All { get; } = new[]
    {
        Auto, Rag, Agent,
    };

    public static ChatModeChoice FromKey(string? key) => (key?.Trim().ToLowerInvariant()) switch
    {
        "agent" => Agent,
        "rag" => Rag,
        "auto" => Auto,
        _ => Auto,
    };
}
