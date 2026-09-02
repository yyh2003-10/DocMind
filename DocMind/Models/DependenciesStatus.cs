using System.Text.Json.Serialization;

namespace DocMind.Models;

/// <summary>运行依赖就绪状态聚合（GET /v1/system/dependencies）。</summary>
public sealed record DependenciesStatus
{
    [property: JsonPropertyName("gpu_available")] public bool GpuAvailable { get; init; }
    [property: JsonPropertyName("gpu_provider")] public string? GpuProvider { get; init; }
    [property: JsonPropertyName("has_nvidia_gpu")] public bool HasNvidiaGpu { get; init; }
    [property: JsonPropertyName("gpu_name")] public string? GpuName { get; init; }
    [property: JsonPropertyName("cuda_runtime_ready")] public bool CudaRuntimeReady { get; init; }
    [property: JsonPropertyName("cuda_runtime_tag")] public string? CudaRuntimeTag { get; init; }
    [property: JsonPropertyName("recommended_path")] public string? RecommendedPath { get; init; }
    [property: JsonPropertyName("ocr_available")] public bool OcrAvailable { get; init; }
    [property: JsonPropertyName("model_cached")] public bool ModelCached { get; init; }
    [property: JsonPropertyName("model_name")] public string? ModelName { get; init; }
    [property: JsonPropertyName("poppler_available")] public bool PopplerAvailable { get; init; }
    [property: JsonPropertyName("installed_packages")] public Dictionary<string, string?>? InstalledPackages { get; init; }
    [property: JsonPropertyName("warnings")] public List<string>? Warnings { get; init; }
    [property: JsonPropertyName("platform")] public string? Platform { get; init; }
    [property: JsonPropertyName("python_version")] public string? PythonVersion { get; init; }
}

/// <summary>嵌入模型下载 SSE 进度帧（POST /v1/system/download-model）。</summary>
public sealed record DownloadProgressFrame
{
    [property: JsonPropertyName("type")] public string Type { get; init; } = "";
    [property: JsonPropertyName("downloaded_files")] public int DownloadedFiles { get; init; }
    [property: JsonPropertyName("total_files")] public int TotalFiles { get; init; }
    [property: JsonPropertyName("downloaded_bytes")] public long DownloadedBytes { get; init; }
    [property: JsonPropertyName("total_bytes")] public long TotalBytes { get; init; }
    [property: JsonPropertyName("model")] public string? Model { get; init; }
    [property: JsonPropertyName("path")] public string? Path { get; init; }
    [property: JsonPropertyName("message")] public string? Message { get; init; }

    /// <summary>下载进度 0.0 ~ 1.0（按字节；无总字节时退化为按文件）。</summary>
    public double Progress => TotalBytes > 0
        ? (double)DownloadedBytes / TotalBytes
        : TotalFiles > 0 ? (double)DownloadedFiles / TotalFiles : 0.0;
}