using System.Diagnostics;
using System.IO;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;

namespace DocMind.ViewModels;

/// <summary>
/// GPU 加速状态管理：后端健康检查返回 GPU 不可用时，在设置页显示警告条。
/// 独立于 SettingsViewModel，通过 DI 注入到 App.xaml.cs 和 SettingsViewModel。
/// </summary>
public partial class GpuWarningViewModel : ViewModelBase
{
    private bool _gpuAvailable;
    private string? _gpuProvider;
    private bool _showWarning;
    private bool _dismissed;

    /// <summary>用户选择"不再提示"时的持久化回调（由 App.xaml.cs 注入）。</summary>
    public Action? OnDismissed { get; set; }

    public GpuWarningViewModel()
    {
        Title = "GPU 加速";
    }

    /// <summary>后端是否可用 GPU 加速嵌入。</summary>
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

    /// <summary>GPU provider 名称（如 "CUDAExecutionProvider"）。</summary>
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

    /// <summary>是否显示警告条（GPU 不可用 + 用户未选择"不再提示"）。</summary>
    public bool ShowWarning
    {
        get => _showWarning;
        private set => SetProperty(ref _showWarning, value);
    }

    /// <summary>用户是否已选择"不再提示"。</summary>
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

    /// <summary>GPU 状态描述文本（用于关于区域显示）。</summary>
    public string GpuStatusText => GpuAvailable
        ? $"{GpuProvider ?? "GPU"}"
        : "不可用（CPU 模式）";

    /// <summary>GPU 状态颜色：可用=绿，不可用=橙。</summary>
    public Brush GpuStatusBrush => GpuAvailable
        ? new SolidColorBrush(Color.FromRgb(56, 161, 105))   // #38A169
        : new SolidColorBrush(Color.FromRgb(214, 158, 46)); // #D69E2E

    /// <summary>打开 GPU 安装指南（部署文档）。</summary>
    [RelayCommand]
    private void OpenGpuInstall() => OpenGpuInstallGuide();

    /// <summary>用户选择"不再提示"。</summary>
    [RelayCommand]
    private void DismissGpuWarning() => DismissWarning();

    /// <summary>从后端健康检查结果更新状态。</summary>
    public void UpdateFromHealth(HealthStatus health)
    {
        GpuAvailable = health.GpuAvailable;
        GpuProvider = health.GpuProvider;

        // 仅当 GPU 不可用且用户未选择"不再提示"时显示警告
        ShowWarning = !GpuAvailable && !Dismissed;
    }

    /// <summary>用户选择"不再提示"。</summary>
    public void DismissWarning()
    {
        Dismissed = true;
        ShowWarning = false;
        OnDismissed?.Invoke();
    }

    /// <summary>打开 GPU 安装指南（部署文档的 GPU 章节）。</summary>
    public void OpenGpuInstallGuide()
    {
        // 尝试打开本地部署文档
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