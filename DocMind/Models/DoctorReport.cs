namespace DocMind.Models;

/// <summary>
/// 单项诊断结果 DTO。
/// </summary>
public sealed class DiagnosticCheckItem
{
    public string Name { get; init; } = string.Empty;
    public string Category { get; init; } = string.Empty;
    public string Status { get; init; } = string.Empty;
    public string Message { get; init; } = string.Empty;
    public string? Detail { get; init; }
    public string? FixSuggestion { get; init; }

    public bool IsOk => Status == "ok";
    public bool IsWarning => Status == "warning";
    public bool IsError => Status == "error";
    public bool IsInfo => Status == "info";

    public string BadgeText => Status switch
    {
        "ok" => "✓ 正常",
        "warning" => "! 待优化",
        "error" => "✗ 异常",
        _ => "i 提示",
    };
}

/// <summary>
/// 系统体检总报告 DTO。
/// </summary>
public sealed class DoctorReportResult
{
    public string OverallStatus { get; init; } = "ok";
    public int Score { get; init; } = 100;
    public string Summary { get; init; } = string.Empty;
    public double Timestamp { get; init; }
    public IReadOnlyList<DiagnosticCheckItem> Checks { get; init; } = Array.Empty<DiagnosticCheckItem>();
}

/// <summary>
/// 示例文档导入结果 DTO。
/// </summary>
public sealed class SampleIngestResult
{
    public bool Ok { get; init; }
    public string Status { get; init; } = string.Empty;
    public string Title { get; init; } = string.Empty;
    public string Collection { get; init; } = "default";
    public int ChunkCount { get; init; }
    public int ElapsedMs { get; init; }
    public string? Error { get; init; }
}
