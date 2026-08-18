using System.Collections.Generic;

namespace DocMind.Models
{
    /// <summary>
    /// GPU 加速环境诊断报告（对应后端 GET /v1/system/gpu-diagnosis）。
    /// </summary>
    public sealed record GpuDiagnosis
    {
        /// <summary>当前是否已启用 GPU 加速（health 上报语义）。</summary>
        public bool GpuAvailable { get; init; }

        /// <summary>生效的 GPU provider（如 CUDAExecutionProvider / DmlExecutionProvider）。</summary>
        public string? GpuProvider { get; init; }

        /// <summary>onnxruntime 实际可用的 providers 列表。</summary>
        public List<string>? EmbedProviders { get; init; }

        /// <summary>是否检测到 NVIDIA 显卡（nvidia-smi 可用）。</summary>
        public bool HasNvidiaGpu { get; init; }

        public string? GpuName { get; init; }

        public string? DriverVersion { get; init; }

        /// <summary>驱动支持的 CUDA 版本（nvidia-smi 头部的 "CUDA Version"）。</summary>
        public string? CudaDriverVersion { get; init; }

        /// <summary>cu12/cu13 运行时 DLL 是否可加载。</summary>
        public bool CudaRuntimeReady { get; init; }

        /// <summary>就绪的运行时标签：cu12 | cu13 | null。</summary>
        public string? CudaRuntimeTag { get; init; }

        public string? PythonVersion { get; init; }

        /// <summary>关键 pip 包版本（未安装为 null）。</summary>
        public Dictionary<string, string?>? InstalledPackages { get; init; }

        /// <summary>推荐安装路径：cuda12|cuda13|directml|paddle-ocr-gpu|cpu。</summary>
        public string RecommendedPath { get; init; } = "cpu";

        /// <summary>环境问题清单（如 CPU 版 onnxruntime 覆盖 GPU 模块）。</summary>
        public List<string>? Warnings { get; init; }

        /// <summary>当前操作系统平台（如 win32 / darwin / linux）。</summary>
        public string? Platform { get; init; }
    }
}