using System.IO;
using System.Text.Json;
using CommunityToolkit.Mvvm.Input;
using Microsoft.Extensions.Configuration;

namespace DocMind.ViewModels;

public partial class SettingsViewModel : ViewModelBase
{
    private readonly AppSettings _appSettings;

    private string _backendUrl;
    private int _pollIntervalMs;
    private int _startupTimeoutSec;
    private string _statusMessage = "就绪";
    private bool _isDirty;

    public SettingsViewModel(AppSettings appSettings)
    {
        _appSettings = appSettings;
        Title = "设置";

        // 加载当前值到可编辑字段
        _backendUrl = _appSettings.BackendUrl;
        _pollIntervalMs = _appSettings.PollIntervalMs;
        _startupTimeoutSec = _appSettings.StartupTimeoutSec;
    }

    /// <summary>后端 FastAPI 地址（含端口）。</summary>
    public string BackendUrl
    {
        get => _backendUrl;
        set => SetDirty(ref _backendUrl, value);
    }

    /// <summary>任务轮询间隔（毫秒）。</summary>
    public int PollIntervalMs
    {
        get => _pollIntervalMs;
        set => SetDirty(ref _pollIntervalMs, value);
    }

    /// <summary>后端启动超时（秒）。</summary>
    public int StartupTimeoutSec
    {
        get => _startupTimeoutSec;
        set => SetDirty(ref _startupTimeoutSec, value);
    }

    /// <summary>底部状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>是否有未保存的改动。</summary>
    public bool IsDirty
    {
        get => _isDirty;
        set
        {
            if (SetProperty(ref _isDirty, value))
            {
                SaveCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>辅助：设置字段并标记为 dirty。</summary>
    private bool SetDirty<T>(ref T field, T value, [System.Runtime.CompilerServices.CallerMemberName] string? name = null)
    {
        var changed = SetProperty(ref field, value, name);
        if (changed)
        {
            IsDirty = true;
        }
        return changed;
    }

    private bool CanSave => IsDirty;

    /// <summary>保存到 appsettings.json 并刷新 AppSettings 单例。</summary>
    [RelayCommand(CanExecute = nameof(CanSave))]
    private async Task SaveAsync()
    {
        if (!CanSave)
        {
            return;
        }

        try
        {
            // 写回内存对象
            _appSettings.BackendUrl = BackendUrl;
            _appSettings.PollIntervalMs = PollIntervalMs;
            _appSettings.StartupTimeoutSec = StartupTimeoutSec;

            // �盘 appsettings.json（与 exe 同目录）
            var settingsPath = System.IO.Path.Combine(
                AppContext.BaseDirectory, "appsettings.json");

            var json = JsonSerializer.Serialize(new
            {
                BackendUrl = BackendUrl,
                PollIntervalMs = PollIntervalMs,
                StartupTimeoutSec = StartupTimeoutSec,
            }, new JsonSerializerOptions { WriteIndented = true });

            await File.WriteAllTextAsync(settingsPath, json);

            IsDirty = false;
            StatusMessage = "已保存（重启后端通信变更生效）";
        }
        catch (Exception ex)
        {
            StatusMessage = $"保存失败：{ex.Message}";
        }
    }

    /// <summary>恢复到加载时的值。</summary>
    [RelayCommand]
    private void Revert()
    {
        BackendUrl = _appSettings.BackendUrl;
        PollIntervalMs = _appSettings.PollIntervalMs;
        StartupTimeoutSec = _appSettings.StartupTimeoutSec;
        IsDirty = false;
        StatusMessage = "已恢复";
    }
}
