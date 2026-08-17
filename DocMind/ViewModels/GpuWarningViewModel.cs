using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

/// <summary>
/// GPU 加速状态管理与一键安装向导。
/// 独立于 SettingsViewModel，通过 DI 注入到 App.xaml.cs 和 SettingsViewModel。
/// 功能：
/// - 健康检查时自动检测 GPU 状态，显示警告条
/// - 设置页「GPU 加速」卡片：诊断 → 推荐 → 一键安装 → 重启后端
/// </summary>
public partial class GpuWarningViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;
    private readonly BackendProcessService _backendService;
    private readonly NotificationService _notifications;

    private bool _gpuAvailable;
    private string? _gpuProvider;
    private bool _showWarning;
    private bool _dismissed;

    // --- 诊断/安装状态 ---
    private GpuDiagnosis? _diagnosis;
    private bool _isDiagnosing;
    private bool _isInstalling;
    private bool _installSucceeded;
    private string _installLog = "";
    private string? _selectedPath;
    private string? _statusMessage;

    private readonly StringBuilder _logBuffer = new();

    /// <summary>用户选择"不再提示"时的持久化回调（由 App.xaml.cs 注入）。</summary>
    public Action? OnDismissed { get; set; }

    public GpuWarningViewModel(
        IDoc2kbApiService apiService,
        BackendProcessService backendService,
        NotificationService notifications)
    {
        _apiService = apiService;
        _backendService = backendService;
        _notifications = notifications;
        Title = "GPU 加速";
    }

    // ========== 健康检查属性（保持原有接口兼容）==========

    public bool GpuAvailable
    {
        get => _gpuAvailable;
        private set
        {
            if (SetProperty(ref _gpuAvailable, value))
            {
                OnPropertyChanged(nameof(GpuStatusText));
                OnPropertyChanged(nameof(GpuStatusBrush));
            }
        }
    }

    public string? GpuProvider
    {
        get => _gpuProvider;
        private set
        {
            if (SetProperty(ref _gpuProvider, value))
            {
                OnPropertyChanged(nameof(GpuStatusText));
            }
        }
    }

    public bool ShowWarning
    {
        get => _showWarning;
        private set => SetProperty(ref _showWarning, value);
    }

    public bool Dismissed
    {
        get => _dismissed;
        set
        {
            if (SetProperty(ref _dismissed, value) && value)
            {
                ShowWarning = false;
            }
        }
    }

    public string GpuStatusText => GpuAvailable
        ? $"{GpuProvider ?? "GPU"}"
        : "不可用（CPU 模式）";

    public Brush GpuStatusBrush => GpuAvailable
        ? new SolidColorBrush(Color.FromRgb(56, 161, 105))   // #38A169
        : new SolidColorBrush(Color.FromRgb(214, 158, 46)); // #D69E2E

    // ========== 诊断属性 ==========

    public GpuDiagnosis? Diagnosis
    {
        get => _diagnosis;
        private set
        {
            if (SetProperty(ref _diagnosis, value))
            {
                OnPropertyChanged(nameof(HasDiagnosis));
                OnPropertyChanged(nameof(GpuDeviceName));
                OnPropertyChanged(nameof(DriverInfo));
                OnPropertyChanged(nameof(Warnings));
                OnPropertyChanged(nameof(InstalledPackagesDisplay));
                OnPropertyChanged(nameof(RecommendedPathHint));
                OnPropertyChanged(nameof(AvailablePaths));
                OnPropertyChanged(nameof(CanInstall));
                // 自动设置推荐路径
                if (value?.RecommendedPath is { Length: > 0 } p)
                    SelectedPath = p;
            }
        }
    }

    public bool HasDiagnosis => Diagnosis is not null;

    public string? GpuDeviceName => Diagnosis?.GpuName;

    /// <summary>驱动版本信息（如 "595.79 (CUDA 13.2)"）。</summary>
    public string DriverInfo
    {
        get
        {
            if (Diagnosis is null) return "";
            var parts = new System.Collections.Generic.List<string>();
            if (!string.IsNullOrWhiteSpace(Diagnosis.DriverVersion))
                parts.Add(Diagnosis.DriverVersion);
            if (!string.IsNullOrWhiteSpace(Diagnosis.CudaDriverVersion))
                parts.Add($"CUDA {Diagnosis.CudaDriverVersion}");
            if (!string.IsNullOrWhiteSpace(Diagnosis.PythonVersion))
                parts.Add($"Python {Diagnosis.PythonVersion}");
            return parts.Count > 0 ? string.Join("  |  ", parts) : "";
        }
    }

    public string CudaRuntimeInfo => Diagnosis switch
    {
        null => "",
        { CudaRuntimeReady: true, CudaRuntimeTag: not null } tag
            => $"已就绪 ({tag.CudaRuntimeTag})",
        _ => "未安装"
    };

    public System.Collections.Generic.List<string> Warnings => Diagnosis?.Warnings ?? new();

    /// <summary>已安装包显示文本。</summary>
    public System.Collections.Generic.List<PackageDisplayItem> InstalledPackagesDisplay
    {
        get
        {
            if (Diagnosis?.InstalledPackages is null) return new();
            var result = new System.Collections.Generic.List<PackageDisplayItem>();
            foreach (var kv in Diagnosis.InstalledPackages)
            {
                result.Add(new PackageDisplayItem
                {
                    Name = kv.Key,
                    Version = kv.Value ?? "未安装",
                    Installed = kv.Value is not null,
                });
            }
            return result;
        }
    }

    public string RecommendedPathHint => Diagnosis?.RecommendedPath switch
    {
        "cuda12" => "CUDA 12（NVIDIA GPU，PyPI 标准 wheel）",
        "cuda13" => "CUDA 13（需 cu13 本地 wheel）",
        "directml" => "DirectML（通用 GPU，AMD / Intel / NVIDIA）",
        "paddle-ocr-gpu" => "PaddlePaddle OCR GPU 加速",
        "cpu" => "当前已是 CPU 模式，无可用 GPU 加速方案",
        _ => ""
    };

    public System.Collections.Generic.List<PathOption> AvailablePaths
    {
        get
        {
            var hasNvidia = Diagnosis?.HasNvidiaGpu == true;
            var paths = new System.Collections.Generic.List<PathOption>();
            if (hasNvidia)
            {
                paths.Add(new("cuda12", "CUDA 12（推荐）", "NVIDIA GPU，PyPI 标准 wheel，覆盖大多数设备"));
                paths.Add(new("cuda13", "CUDA 13", "需本地 cu13 wheel，仅限特定驱动版本"));
            }
            paths.Add(new("directml", "DirectML", "通用 GPU（AMD / Intel / NVIDIA），无需 CUDA"));
            paths.Add(new("paddle-ocr-gpu", "PaddlePaddle OCR GPU", "OCR 文字识别加速，需 CUDA 运行时"));
            return paths;
        }
    }

    // ========== 安装状态属性 ==========

    public bool IsDiagnosing
    {
        get => _isDiagnosing;
        private set
        {
            if (SetProperty(ref _isDiagnosing, value))
                OnPropertyChanged(nameof(CanInstall));
        }
    }

    public bool IsInstalling
    {
        get => _isInstalling;
        private set
        {
            if (SetProperty(ref _isInstalling, value))
                OnPropertyChanged(nameof(CanInstall));
        }
    }

    public string InstallLog
    {
        get => _installLog;
        private set => SetProperty(ref _installLog, value);
    }

    public string? SelectedPath
    {
        get => _selectedPath;
        set
        {
            if (SetProperty(ref _selectedPath, value))
                OnPropertyChanged(nameof(CanInstall));
        }
    }

    public bool CanInstall => !IsInstalling && !IsDiagnosing && SelectedPath is { Length: > 0 };

    public bool CanRestart => _installSucceeded && !IsInstalling;

    public string? StatusMessage
    {
        get => _statusMessage;
        private set => SetProperty(ref _statusMessage, value);
    }

    // ========== 命令 ==========

    [RelayCommand]
    private void OpenGpuInstall() => OpenGpuInstallGuide();

    [RelayCommand]
    private void DismissGpuWarning() => DismissWarning();

    [RelayCommand]
    public async Task DiagnoseAsync()
    {
        if (IsDiagnosing || IsInstalling) return;
        IsDiagnosing = true;
        StatusMessage = "正在连接后端检测 GPU 环境...";
        try
        {
            var result = await _apiService.GetGpuDiagnosisAsync();
            Diagnosis = result;
            GpuAvailable = result.GpuAvailable;
            GpuProvider = result.GpuProvider;
            ShowWarning = !GpuAvailable && !Dismissed;
            StatusMessage = result.GpuAvailable ? "GPU 加速已启用" : "未启用 GPU 加速";
        }
        catch (BackendConnectionException)
        {
            StatusMessage = "后端服务未启动或不可达，请先点击顶栏「启动服务」";
            _notifications.Warning("后端服务未连接，请先点击顶栏【启动服务】后再进行 GPU 诊断与安装", "GPU 加速");
        }
        catch (Exception ex)
        {
            StatusMessage = $"诊断失败: {ex.Message}";
            _notifications.Error($"GPU 诊断失败：{ex.Message}", "GPU 加速");
        }
        finally
        {
            IsDiagnosing = false;
        }
    }

    [RelayCommand]
    private async Task InstallGpuAsync()
    {
        if (IsInstalling || SelectedPath is not { Length: > 0 } path) return;
        if (path == "cpu") return;
        IsInstalling = true;
        _installSucceeded = false;
        OnPropertyChanged(nameof(CanRestart));
        _logBuffer.Clear();
        InstallLog = "";
        StatusMessage = "正在安装...";

        try
        {
            await _apiService.InstallGpuAsync(
                path,
                onLog: line =>
                {
                    _logBuffer.AppendLine(line);
                    // 回调来自 SSE 后台线程，必须 dispatcher 回 UI 线程更新绑定
                    Application.Current?.Dispatcher.Invoke(() =>
                    {
                        InstallLog = _logBuffer.ToString();
                    });
                },
                onDone: success =>
                {
                    _installSucceeded = success;
                    Application.Current?.Dispatcher.Invoke(() =>
                    {
                        StatusMessage = success
                            ? "安装完成，请点击「重启后端」生效"
                            : "安装失败，请查看上方日志排查";
                        OnPropertyChanged(nameof(CanRestart));
                    });
                });
        }
        catch (OperationCanceledException)
        {
            StatusMessage = "安装已取消";
        }
        catch (Exception ex)
        {
            StatusMessage = $"安装异常: {ex.Message}";
            _logBuffer.AppendLine($"[异常] {ex.Message}");
            InstallLog = _logBuffer.ToString();
            _notifications.Error($"GPU 安装异常：{ex.Message}", "GPU 加速");
        }
        finally
        {
            IsInstalling = false;
        }
    }

    [RelayCommand(CanExecute = nameof(CanRestart))]
    private async Task RestartBackendAsync()
    {
        StatusMessage = "正在重启后端...";
        try
        {
            await _backendService.StopAsync();
            await _backendService.StartAsync();
            // 重启后重新诊断
            await DiagnoseAsync();
            _notifications.Success("后端已重启", "GPU 加速");
        }
        catch (Exception ex)
        {
            StatusMessage = $"重启失败: {ex.Message}";
            _notifications.Error($"后端重启失败：{ex.Message}", "GPU 加速");
        }
    }

    [RelayCommand]
    private void ClearLog()
    {
        _logBuffer.Clear();
        InstallLog = "";
        StatusMessage = null;
    }

    // ========== 从健康检查更新（保持向后兼容）==========

    public void UpdateFromHealth(HealthStatus health)
    {
        GpuAvailable = health.GpuAvailable;
        GpuProvider = health.GpuProvider;
        ShowWarning = !GpuAvailable && !Dismissed;
    }

    public void DismissWarning()
    {
        Dismissed = true;
        ShowWarning = false;
        OnDismissed?.Invoke();
    }

    private void OpenGpuInstallGuide()
    {
        var candidates = new[]
        {
            Path.Combine(AppContext.BaseDirectory, "docs", "部署指南.md"),
            Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "docs", "部署指南.md"),
        };
        foreach (var path in candidates)
        {
            var fullPath = Path.GetFullPath(path);
            if (File.Exists(fullPath))
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = fullPath,
                    UseShellExecute = true,
                });
                return;
            }
        }
    }
}

/// <summary>已安装包显示项。</summary>
public sealed class PackageDisplayItem
{
    public string Name { get; set; } = "";
    public string Version { get; set; } = "";
    public bool Installed { get; set; }
}

/// <summary>可选安装路径。</summary>
public sealed class PathOption
{
    public string Id { get; }
    public string Label { get; }
    public string Description { get; }

    public PathOption(string id, string label, string description)
    {
        Id = id;
        Label = label;
        Description = description;
    }
}