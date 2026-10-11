namespace DocMind.Models;

/// <summary>终态 Agent 后台任务结果，可在客户端重连后直接恢复到对话气泡。</summary>
public sealed record AgentJobResult
{
    public string Answer { get; init; } = string.Empty;
    public string ChatId { get; init; } = string.Empty;
    public string Model { get; init; } = string.Empty;
    public string Provider { get; init; } = string.Empty;
    public int TotalChunks { get; init; }
    public int ElapsedMs { get; init; }
    public IReadOnlyList<SourceRef> Sources { get; init; } = [];
    public IReadOnlyList<AgentTrajectoryStep> Trajectory { get; init; } = [];
    public string? TrajectoryWarning { get; init; }
    public bool Partial { get; init; }
    public string? Warning { get; init; }
    public IReadOnlyList<Dictionary<string, object?>> Artifacts { get; init; } = [];
}
