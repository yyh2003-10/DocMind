using System.Text.Json.Serialization;

namespace DocMind.Models;

/// <summary>Agent 轨迹单步（loop transcript / done.trajectory）。</summary>
public sealed record AgentTrajectoryStep
{
    [JsonPropertyName("step")]
    public int Step { get; init; }

    /// <summary>model | tool_result 等。</summary>
    [JsonPropertyName("type")]
    public string Type { get; init; } = "";

    [JsonPropertyName("tool_id")]
    public string? ToolId { get; init; }

    [JsonPropertyName("call_id")]
    public string? CallId { get; init; }

    [JsonPropertyName("status")]
    public string? Status { get; init; }

    [JsonPropertyName("summary")]
    public string? Summary { get; init; }

    [JsonPropertyName("final_text")]
    public string? FinalText { get; init; }

    [JsonIgnore]
    public string DisplayText
    {
        get
        {
            if (Type == "tool_result" || !string.IsNullOrEmpty(ToolId))
            {
                var icon = Status == "denied" ? "⛔" : Status == "error" ? "⚠" : "✔";
                var label = string.IsNullOrWhiteSpace(Summary) ? (Status ?? "") : Summary!;
                return $"{icon} {ToolId}{(string.IsNullOrWhiteSpace(label) ? "" : $" · {label}")}";
            }
            var text = FinalText ?? "";
            if (text.Length > 80) text = text[..80] + "…";
            return string.IsNullOrWhiteSpace(text) ? $"步骤 {Step}" : $"💭 {text}";
        }
    }
}
