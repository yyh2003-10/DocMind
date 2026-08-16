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
}
