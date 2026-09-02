namespace DocMind.Models;

public sealed record HealthStatus
{
    public string Status { get; init; } = string.Empty;
    public string? Version { get; init; }
    public double? UptimeSeconds { get; init; }

    /// <summary>是否可用 GPU 加速嵌入（CUDA / DirectML）。</summary>
    public bool GpuAvailable { get; init; }

    /// <summary>实际使用的 GPU provider（如 "CUDAExecutionProvider"）。</summary>
    public string? GpuProvider { get; init; }

    /// <summary>嵌入推理实际使用的 ONNX Runtime providers 列表。</summary>
    public List<string>? EmbedProviders { get; init; }

    /// <summary>数据库连接 + sqlite-vec 扩展是否可用（后端 /v1/health 真实探测结果）。
    /// false 时 Status 为 "degraded"，前端状态灯应降级为黄色警告。</summary>
    public bool StoreOk { get; init; } = true;

    /// <summary>存储不可用时的错误描述（如 "数据库连接或 sqlite-vec 扩展不可用"）；null = 正常。</summary>
    public string? StoreError { get; init; }
}
