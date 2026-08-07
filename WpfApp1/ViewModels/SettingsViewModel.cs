using System.IO;
using System.Text.Json;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;
using Microsoft.Extensions.Configuration;

namespace DocMind.ViewModels;

public partial class SettingsViewModel : ViewModelBase
{
    private readonly AppSettings _appSettings;
    private readonly NotificationService _notifications;
    private readonly ThemeService _themeService;
    private readonly IDoc2kbApiService _apiService;

    private string _backendUrl;
    private int _pollIntervalMs;
    private int _startupTimeoutSec;
    private string? _backendCommand;
    private bool _autoStartBackend = true;
    private bool _stopBackendOnExit = true;
    private string? _autoIngestPath;
    private string _autoIngestCollection = "default";
    private bool _autoIngestRecursive;
    private string _embedModel;
    private string? _embedModelPath;
    private int? _chunkMaxTokens;
    private int? _chunkMinChars;
    private int? _chunkOverlapChars;
    private int? _chunkMaxChars;
    private string _statusMessage = "就绪";
    private bool _isDirty;

    public SettingsViewModel(
        AppSettings appSettings,
        NotificationService notifications,
        ThemeService themeService,
        IDoc2kbApiService apiService)
    {
        _appSettings = appSettings;
        _notifications = notifications;
        _themeService = themeService;
        _apiService = apiService;
        Title = "设置";

        // 加载当前值到可编辑字段
        _backendUrl = _appSettings.BackendUrl;
        _pollIntervalMs = _appSettings.PollIntervalMs;
        _startupTimeoutSec = _appSettings.StartupTimeoutSec;
        _backendCommand = _appSettings.BackendCommand;
        _autoStartBackend = _appSettings.AutoStartBackend;
        _stopBackendOnExit = _appSettings.StopBackendOnExit;
        _autoIngestPath = _appSettings.AutoIngestPath;
        _autoIngestCollection = _appSettings.AutoIngestCollection;
        _autoIngestRecursive = _appSettings.AutoIngestRecursive;
        _embedModel = _appSettings.EmbedModel;
        _embedModelPath = _appSettings.EmbedModelPath;
        _chunkMaxTokens = _appSettings.ChunkMaxTokens;
        _chunkMinChars = _appSettings.ChunkMinChars;
        _chunkOverlapChars = _appSettings.ChunkOverlapChars;
        _chunkMaxChars = _appSettings.ChunkMaxChars;
    }

    /// <summary>当前主题（选择即切换）。</summary>
    public ThemeMode SelectedTheme
    {
        get => _themeService.CurrentTheme;
        set
        {
            if (value != _themeService.CurrentTheme)
            {
                _themeService.ApplyTheme(value);
                OnPropertyChanged();
            }
        }
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

    /// <summary>拉起后端用的命令（绝对路径优先；空走自动探测）。</summary>
    public string? BackendCommand
    {
        get => _backendCommand;
        set => SetDirty(ref _backendCommand, value);
    }

    /// <summary>启动 WPF 时自动拉起后端子进程（false = 仅接外部已运行的后端）。</summary>
    public bool AutoStartBackend
    {
        get => _autoStartBackend;
        set => SetDirty(ref _autoStartBackend, value);
    }

    /// <summary>WPF 退出时联动终止后端子进程（false = 退出后保留后端继续运行）。</summary>
    public bool StopBackendOnExit
    {
        get => _stopBackendOnExit;
        set => SetDirty(ref _stopBackendOnExit, value);
    }

    /// <summary>启动时自动 ingest 的目录路径（空表示不自动导入）。</summary>
    public string? AutoIngestPath
    {
        get => _autoIngestPath;
        set => SetDirty(ref _autoIngestPath, value);
    }

    /// <summary>自动 ingest 用的集合名（默认 default）。</summary>
    public string AutoIngestCollection
    {
        get => _autoIngestCollection;
        set => SetDirty(ref _autoIngestCollection, value);
    }

    /// <summary>自动 ingest 目录时是否递归子目录。</summary>
    public bool AutoIngestRecursive
    {
        get => _autoIngestRecursive;
        set => SetDirty(ref _autoIngestRecursive, value);
    }

    /// <summary>嵌入模型名（后端 DOC2MIND_EMBED_MODEL）。</summary>
    public string EmbedModel
    {
        get => _embedModel;
        set => SetDirty(ref _embedModel, value);
    }

    /// <summary>本地模型目录（后端 DOC2MIND_EMBED_MODEL_PATH）；空 = 用 EmbedModel 联网下载。</summary>
    public string? EmbedModelPath
    {
        get => _embedModelPath;
        set => SetDirty(ref _embedModelPath, value);
    }

    /// <summary>分块最大 token 数（后端 DOC2MIND_CHUNK_MAX_TOKENS）。</summary>
    public int? ChunkMaxTokens
    {
        get => _chunkMaxTokens;
        set => SetDirty(ref _chunkMaxTokens, value);
    }

    /// <summary>分块最小字符数（后端 DOC2MIND_CHUNK_MIN_CHARS）。</summary>
    public int? ChunkMinChars
    {
        get => _chunkMinChars;
        set => SetDirty(ref _chunkMinChars, value);
    }

    /// <summary>分块重叠字符数（后端 DOC2MIND_CHUNK_OVERLAP_CHARS）。</summary>
    public int? ChunkOverlapChars
    {
        get => _chunkOverlapChars;
        set => SetDirty(ref _chunkOverlapChars, value);
    }

    /// <summary>分块最大字符数（后端 DOC2MIND_CHUNK_MAX_CHARS）。</summary>
    public int? ChunkMaxChars
    {
        get => _chunkMaxChars;
        set => SetDirty(ref _chunkMaxChars, value);
    }

    /// <summary>可选的嵌入模型清单（设置页下拉；与后端 catalog 一致，均为 fastembed 实际支持）。</summary>
    public IReadOnlyList<string> SupportedEmbedModels { get; } = new[]
    {
        "BAAI/bge-small-zh-v1.5",
        "BAAI/bge-small-en-v1.5",
        "BAAI/bge-base-en-v1.5",
        "BAAI/bge-large-en-v1.5",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "jinaai/jina-embeddings-v2-base-zh",
        "intfloat/multilingual-e5-large",
    };

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

        DebugLog.Info(
            $"保存设置: BackendUrl='{BackendUrl}' PollIntervalMs={PollIntervalMs} StartupTimeoutSec={StartupTimeoutSec} " +
            $"AutoStartBackend={AutoStartBackend} AutoIngestPath='{AutoIngestPath}' AutoIngestCollection='{AutoIngestCollection}' AutoIngestRecursive={AutoIngestRecursive}",
            "Settings");

        try
        {
            // 写回内存对象
            _appSettings.BackendUrl = BackendUrl;
            _appSettings.PollIntervalMs = PollIntervalMs;
            _appSettings.StartupTimeoutSec = StartupTimeoutSec;
            _appSettings.BackendCommand = BackendCommand;
            _appSettings.AutoStartBackend = AutoStartBackend;
            _appSettings.StopBackendOnExit = StopBackendOnExit;
            _appSettings.AutoIngestPath = AutoIngestPath;
            _appSettings.AutoIngestCollection = AutoIngestCollection;
            _appSettings.AutoIngestRecursive = AutoIngestRecursive;
            _appSettings.EmbedModel = EmbedModel;
            _appSettings.EmbedModelPath = EmbedModelPath;
            _appSettings.ChunkMaxTokens = ChunkMaxTokens;
            _appSettings.ChunkMinChars = ChunkMinChars;
            _appSettings.ChunkOverlapChars = ChunkOverlapChars;
            _appSettings.ChunkMaxChars = ChunkMaxChars;

            // 落盘 appsettings.json（与 exe 同目录）
            var settingsPath = System.IO.Path.Combine(
                AppContext.BaseDirectory, "appsettings.json");

            var json = JsonSerializer.Serialize(new
            {
                BackendUrl = BackendUrl,
                PollIntervalMs = PollIntervalMs,
                StartupTimeoutSec = StartupTimeoutSec,
                BackendCommand = BackendCommand,
                AutoStartBackend = AutoStartBackend,
                StopBackendOnExit = StopBackendOnExit,
                AutoIngestPath = AutoIngestPath,
                AutoIngestCollection = AutoIngestCollection,
                AutoIngestRecursive = AutoIngestRecursive,
                EmbedModel = EmbedModel,
                EmbedModelPath = EmbedModelPath,
                ChunkMaxTokens = ChunkMaxTokens,
                ChunkMinChars = ChunkMinChars,
                ChunkOverlapChars = ChunkOverlapChars,
                ChunkMaxChars = ChunkMaxChars,
                Theme = _appSettings.Theme,
            }, new JsonSerializerOptions { WriteIndented = true });

            await File.WriteAllTextAsync(settingsPath, json);

            // 推送到后端运行时配置（/v1/config），免重启生效；
            // 后端不可达时仅告警，不阻断本地保存（重启后端后环境变量也会生效）。
            try
            {
                var pushed = await _apiService.UpdateConfigAsync(new BackendConfigUpdate
                {
                    // 空模型名不推送，避免清空后端配置
                    EmbedModel = string.IsNullOrWhiteSpace(EmbedModel) ? null : EmbedModel.Trim(),
                    // 本地模型目录（空字符串 = 清除本地模型，回到联网模型）
                    EmbedModelPath = string.IsNullOrWhiteSpace(EmbedModelPath) ? "" : EmbedModelPath.Trim(),
                    EmbedBatchSize = null, // 前端暂不暴露
                    ChunkMaxTokens = ChunkMaxTokens,
                    ChunkMinChars = ChunkMinChars,
                    ChunkOverlapChars = ChunkOverlapChars,
                    ChunkMaxChars = ChunkMaxChars,
                    SearchTopK = null,     // 前端暂不暴露
                    RrfK = null,           // 前端暂不暴露
                });
                // 后端提示（如切换模型后维度变化需重建索引）
                if (!string.IsNullOrWhiteSpace(pushed.Notice))
                {
                    StatusMessage = pushed.Notice;
                    _notifications.Warning(pushed.Notice, "模型已切换");
                    DebugLog.Warn($"后端提示: {pushed.Notice}", "Settings");
                }
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"后端参数推送失败（重启后端后仍会生效）: {ex.Message}", "Settings");
            }

            IsDirty = false;
            StatusMessage = "已保存（模型/分块参数已实时生效；其余变更重启后端生效）";
            _notifications.Success("设置已保存");
            DebugLog.Info($"设置保存成功: {settingsPath}", "Settings");
        }
        catch (Exception ex)
        {
            StatusMessage = $"保存失败：{ex.Message}";
            _notifications.Error($"保存失败：{ex.Message}");
            DebugLog.Error($"设置保存失败: {ex.Message}", "Settings", ex);
        }
    }

    /// <summary>恢复到加载时的值。</summary>
    [RelayCommand]
    private void Revert()
    {
        BackendUrl = _appSettings.BackendUrl;
        PollIntervalMs = _appSettings.PollIntervalMs;
        StartupTimeoutSec = _appSettings.StartupTimeoutSec;
        BackendCommand = _appSettings.BackendCommand;
        AutoStartBackend = _appSettings.AutoStartBackend;
        StopBackendOnExit = _appSettings.StopBackendOnExit;
        AutoIngestPath = _appSettings.AutoIngestPath;
        AutoIngestCollection = _appSettings.AutoIngestCollection;
        AutoIngestRecursive = _appSettings.AutoIngestRecursive;
        EmbedModel = _appSettings.EmbedModel;
        EmbedModelPath = _appSettings.EmbedModelPath;
        ChunkMaxTokens = _appSettings.ChunkMaxTokens;
        ChunkMinChars = _appSettings.ChunkMinChars;
        ChunkOverlapChars = _appSettings.ChunkOverlapChars;
        ChunkMaxChars = _appSettings.ChunkMaxChars;
        IsDirty = false;
        StatusMessage = "已恢复";
    }

    [RelayCommand]
    private void SetLightTheme() => SelectedTheme = ThemeMode.Light;

    [RelayCommand]
    private void SetDarkTheme() => SelectedTheme = ThemeMode.Dark;
}
