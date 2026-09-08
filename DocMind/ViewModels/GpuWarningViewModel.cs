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
    private readonly IPluginInstallService _pluginInstaller;
    private readonly NotificationService _notifications;
    private CancellationTokenSource? _installCts;

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
        IPluginInstallService pluginInstaller,
        NotificationService notifications)
    {
        _apiService = apiService;
        _backendService = backendService;
        _pluginInstaller = pluginInstaller;
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
                OnPropertyChanged(nameof(CanShowInstallSection));
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

    public string GpuStatusText
    {
        get
        {
            if (GpuAvailable)
            {
                return $"{GpuProvider ?? "GPU"} 硬件加速";
            }
            if (Diagnosis?.HasNvidiaGpu == true)
            {
                return "CPU 模式 (已检测到独显，可开启 GPU 加速)";
            }
            return "CPU 轻量模式 (纯本地运行)";
        }
    }

    public Brush GpuStatusBrush => GpuAvailable
        ? new SolidColorBrush(Color.FromRgb(56, 161, 105))   // #38A169 (绿)
        : (Diagnosis?.HasNvidiaGpu == true
            ? new SolidColorBrush(Color.FromRgb(49, 130, 206)) // #3182CE (蓝 - 提示有加速潜力)
            : new SolidColorBrush(Color.FromRgb(113, 128, 150))); // #718096 (中性灰/蓝 - 轻量稳定)

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

    /// <summary>推荐路径提示。注意：后端 get_gpu_diagnosis 只返回
    /// coreml/cuda12/cuda13/directml/cpu，OCR 路径由用户在方案下拉框手动选择。</summary>
    public string RecommendedPathHint => Diagnosis?.RecommendedPath switch
    {
        "cuda12" => "CUDA 12（NVIDIA GPU，PyPI 标准 wheel）",
        "cuda13" => "CUDA 13（需 cu13 本地 wheel）",
        "directml" => "DirectML（通用 GPU，AMD / Intel / NVIDIA）",
        "coreml" => "CoreML（Apple Silicon）",
        "cpu" => "当前已是 CPU 模式；扫描件/图片识别请手动选择下方「OCR 文字识别」方案安装",
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
            // OCR 扩展：CPU 版对所有设备可用（扫描 PDF / 图片识别的必需组件），
            // GPU 版仅在有 NVIDIA 独显时提供
            paths.Add(new("ocr-cpu", "OCR 文字识别（CPU）", "扫描件/图片文字识别，无需独显，所有设备可用"));
            if (hasNvidia)
                paths.Add(new("paddle-ocr-gpu", "OCR 文字识别（GPU 加速）", "PaddlePaddle GPU 版，大批量扫描件识别提速"));
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
            {
                OnPropertyChanged(nameof(CanInstall));
                OnPropertyChanged(nameof(CanCancelInstall));
            }
        }
    }

    public string InstallLog
    {
        get => _installLog;
        private set
        {
            if (SetProperty(ref _installLog, value))
            {
                OnPropertyChanged(nameof(HasInstallLog));
                OnPropertyChanged(nameof(CanClearLog));
            }
        }
    }

    public bool HasInstallLog => !string.IsNullOrWhiteSpace(InstallLog);

    public bool CanClearLog => HasInstallLog && !IsInstalling;

    private bool _showAdvancedOptions;
    public bool ShowAdvancedOptions
    {
        get => _showAdvancedOptions;
        set
        {
            if (SetProperty(ref _showAdvancedOptions, value))
            {
                OnPropertyChanged(nameof(CanShowInstallSection));
                OnPropertyChanged(nameof(AdvancedOptionsToggleText));
            }
        }
    }

    /// <summary>是否展示安装方案与包列表（未启用 GPU 或用户主动展开时）。</summary>
    public bool CanShowInstallSection => !GpuAvailable || ShowAdvancedOptions;

    public string AdvancedOptionsToggleText => ShowAdvancedOptions
        ? "收起加速方案与配置 ▲"
        : "⚙ 切换加速方案 / 增装 OCR 加速组件 ▼";

    public string? SelectedPath
    {
        get => _selectedPath;
        set
        {
            if (SetProperty(ref _selectedPath, value))
            {
                OnPropertyChanged(nameof(CanInstall));
                OnPropertyChanged(nameof(InstallButtonText));
            }
        }
    }

    /// <summary>安装按钮文案：按所选方案区分，避免装 OCR 时显示「安装 GPU 加速」误导。</summary>
    public string InstallButtonText => SelectedPath switch
    {
        "ocr-cpu" or "paddle-ocr-gpu" => "⬇ 安装 OCR 组件",
        null => "🚀 一键安装",
        _ => "🚀 一键安装 GPU 加速",
    };

    public bool CanInstall => !IsInstalling && !IsDiagnosing && SelectedPath is { Length: > 0 };

    /// <summary>安装中允许取消（终止独立安装进程并自动恢复后端）。</summary>
    public bool CanCancelInstall => IsInstalling;

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
    private void ToggleAdvancedOptions() => ShowAdvancedOptions = !ShowAdvancedOptions;

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
        _installCts = new CancellationTokenSource();
        var ct = _installCts.Token;
        IsInstalling = true;
        _installSucceeded = false;
        OnPropertyChanged(nameof(CanRestart));
        _logBuffer.Clear();
        InstallLog = "";

        // 独立进程安装需要先停止后端：后端进程自身加载的 numpy/paddle/onnxruntime
        // 运行库 DLL 会让 pip 的文件替换必然失败（WinError 5「拒绝访问」）。
        // 维护模式：阻止 App 自动启动/其他路径在安装窗口内并发拉起后端，
        // 并让 App 级「启动失败」弹窗对这次主动停止保持静默。
        var stoppedBackend = false;
        var cancelled = false;
        _backendService.BeginMaintenance();
        try
        {
            if (_pluginInstaller.CanInstallOutOfProcess)
            {
                StatusMessage = "正在停止后端服务（安装需独占 Python 环境，避免文件占用）...";
                try
                {
                    await _backendService.StopAsync(ct);
                }
                catch (OperationCanceledException)
                {
                    cancelled = true;
                    throw;
                }
                catch (Exception stopEx)
                {
                    DebugLog.Warn($"停止后端失败（继续尝试安装）: {stopEx.Message}", "Install");
                }
                stoppedBackend = true;
                StatusMessage = "正在安装（独立进程，后端已暂停，完成后自动重启）...";
                var ok = await _pluginInstaller.InstallAsync(path, OnInstallLog, ct);
                _installSucceeded = ok;
                StatusMessage = ok ? "安装完成，正在重启后端..." : "安装失败，请查看上方日志排查";
            }
            else
            {
                // 回退：无法解析独立 python 时走后端 SSE
                // （后端已内置运行库占用预检，会直接拒绝并提示）
                StatusMessage = "正在安装...";
                var isOcrPath = path is "ocr-cpu" or "paddle-ocr-gpu";
                var installTask = isOcrPath
                    ? _apiService.InstallOcrAsync(path, onLog: OnInstallLog, onDone: OnInstallDone, ct)
                    : _apiService.InstallGpuAsync(path, onLog: OnInstallLog, onDone: OnInstallDone, ct);
                await installTask;
            }
        }
        catch (OperationCanceledException)
        {
            cancelled = true;
            _logBuffer.AppendLine("[提示] 安装已取消。环境可能处于半安装状态，重新执行一次安装即可覆盖修复。");
            InstallLog = _logBuffer.ToString();
        }
        catch (Exception ex)
        {
            StatusMessage = $"安装异常: {ex.Message}";
            _logBuffer.AppendLine($"[异常] {ex.Message}");
            InstallLog = _logBuffer.ToString();
            _notifications.Error($"安装异常：{ex.Message}", "环境自检");
        }
        finally
        {
            IsInstalling = false;
            if (stoppedBackend)
            {
                // 先退出维护模式再重启：排队的 App 自动启动会被下面的 StartAsync 抢先满足；
                // 恢复后把安装终态写回（重启/诊断动作会覆盖 StatusMessage，终态不能丢）
                var finalMessage = cancelled
                    ? "安装已取消（后端已恢复）"
                    : _installSucceeded
                        ? "安装完成，后端已重启并刷新诊断"
                        : "安装失败，请查看上方日志排查（后端已恢复）";
                StatusMessage = "正在恢复后端服务...";
                _backendService.EndMaintenance();
                try
                {
                    await _backendService.StartAsync(ct: CancellationToken.None);
                }
                catch (Exception restartEx)
                {
                    StatusMessage = $"后端恢复失败: {restartEx.Message}（可点击顶栏「启动服务」手动恢复）";
                    _notifications.Error($"后端恢复失败：{restartEx.Message}", "环境自检");
                }
                StatusMessage = finalMessage;
            }
            else
            {
                // 未走到停后端分支（如校验拦截），也要释放维护标志
                _backendService.EndMaintenance();
            }
            OnPropertyChanged(nameof(CanRestart));
            // 重新诊断刷新包状态（仅安装成功后端在线时才有意义）
            if (_installSucceeded && !cancelled)
            {
                await DiagnoseAsync();
            }
        }
    }

    [RelayCommand]
    private void CancelInstall()
    {
        if (!IsInstalling) return;
        StatusMessage = "正在取消安装...";
        _installCts?.Cancel();
    }

    /// <summary>SSE 安装日志回调（后台线程 → Dispatcher 回 UI 线程）。</summary>
    private void OnInstallLog(string line)
    {
        _logBuffer.AppendLine(line);
        Application.Current?.Dispatcher.Invoke(() =>
        {
            InstallLog = _logBuffer.ToString();
        });
    }

    /// <summary>SSE 安装完成回调（后台线程 → Dispatcher 回 UI 线程）。</summary>
    private void OnInstallDone(bool success)
    {
        _installSucceeded = success;
        Application.Current?.Dispatcher.Invoke(() =>
        {
            StatusMessage = success
                ? "安装完成，请点击「重启后端」生效"
                : "安装失败，请查看上方日志排查";
            OnPropertyChanged(nameof(CanRestart));
        });
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