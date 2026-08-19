using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;
using Microsoft.Extensions.Configuration;

namespace DocMind.ViewModels;

public enum SettingsCategory
{
    AiModels,       // 🤖 AI 模型与对话
    KnowledgeBase,  // 📚 知识库与检索
    Appearance,     // 🎨 界面与外观
    Hardware,       // 🔌 算力与体检
    AboutService,   // 🌐 服务与关于
}

public partial class SettingsViewModel : ViewModelBase
{
    private readonly AppSettings _appSettings;
    private readonly NotificationService _notifications;
    private readonly ThemeService _themeService;
    private readonly IDoc2kbApiService _apiService;
    private readonly GpuWarningViewModel _gpuWarning;
    private readonly BackendProcessService? _backendProcess;

    private SettingsCategory _selectedCategory = SettingsCategory.AiModels;
    public SettingsCategory SelectedCategory
    {
        get => _selectedCategory;
        set
        {
            if (SetProperty(ref _selectedCategory, value))
            {
                OnPropertyChanged(nameof(IsAiModelsSelected));
                OnPropertyChanged(nameof(IsKnowledgeBaseSelected));
                OnPropertyChanged(nameof(IsAppearanceSelected));
                OnPropertyChanged(nameof(IsHardwareSelected));
                OnPropertyChanged(nameof(IsAboutServiceSelected));
            }
        }
    }

    public bool IsAiModelsSelected => SelectedCategory == SettingsCategory.AiModels;
    public bool IsKnowledgeBaseSelected => SelectedCategory == SettingsCategory.KnowledgeBase;
    public bool IsAppearanceSelected => SelectedCategory == SettingsCategory.Appearance;
    public bool IsHardwareSelected => SelectedCategory == SettingsCategory.Hardware;
    public bool IsAboutServiceSelected => SelectedCategory == SettingsCategory.AboutService;

    [RelayCommand]
    public void SelectCategory(string categoryName)
    {
        if (Enum.TryParse<SettingsCategory>(categoryName, true, out var cat))
        {
            SelectedCategory = cat;
        }
    }

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
    private string? _hfEndpoint;
    private int? _chunkMaxTokens;
    private int? _chunkMinChars;
    private int? _chunkOverlapChars;
    private int? _chunkMaxChars;
    // --- LLM ---
    private string _llmProvider = "none";
    private string? _llmApiKey;
    private string? _llmBaseUrl;
    private string _llmModel = "";
    private double _llmTemperature = 0.7;
    private int _llmMaxTokens = 2048;
    private int _ragTopK = 5;
    private string? _ragSystemPrompt;
    private int _ragMaxHistoryTokens = 4096;
    private string _statusMessage = "就绪";
    private bool _isDirty;
    private bool _isTestingConnection;
    private bool _isFetchingModels;
    // 加载/上次保存时的 key/base_url/model/system_prompt 快照：base_url/model/system_prompt 用于推送清除语义
    // （曾配置过+现清空 → 推 "" 显式清除）；key 用于「清除」按钮可见性（与后端 llm_api_key_configured 合并判断）
    private string? _savedApiKeyAtLoad;
    private string? _savedBaseUrlAtLoad;
    private string _savedModelAtLoad = "";
    private string? _savedRagSystemPromptAtLoad;
    // 后端报告的 API Key 已配置状态（/v1/config 的 llm_api_key_configured）。
    // 补充本地判断：本地 appsettings 无 key 但后端有（环境变量/手动配置）时也应允许清除
    private bool _backendApiKeyConfigured;
    // 用户点了「清除 Key」按钮：保存时本地置空 + 后端显式清除。
    // key 输入框留空 ≠ 清除（留空 = 保留原值，与 UI ToolTip 承诺一致）
    private bool _clearApiKeyRequested;

    public SettingsViewModel(
        AppSettings appSettings,
        NotificationService notifications,
        ThemeService themeService,
        IDoc2kbApiService apiService,
        GpuWarningViewModel gpuWarning,
        BackendProcessService? backendProcess = null)
    {
        _appSettings = appSettings;
        _notifications = notifications;
        _themeService = themeService;
        _apiService = apiService;
        _gpuWarning = gpuWarning;
        _backendProcess = backendProcess;
        Title = "设置";
        
        _gpuWarning.PropertyChanged += (s, e) =>
        {
            if (e.PropertyName == nameof(GpuWarningViewModel.Dismissed))
            {
                IsDirty = true;
            }
        };

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
        // 配置里的模型不在推荐清单（用户手动设过）：补一个自定义项，避免下拉选中态空白
        if (!string.IsNullOrWhiteSpace(_embedModel) && _embedModelOptions.All(o => o.ModelId != _embedModel))
        {
            _embedModelOptions.Insert(0, new EmbedModelOption($"自定义（{_embedModel}）", _embedModel));
        }
        _embedModelPath = _appSettings.EmbedModelPath;
        _hfEndpoint = _appSettings.HfEndpoint;
        _chunkMaxTokens = _appSettings.ChunkMaxTokens;
        _chunkMinChars = _appSettings.ChunkMinChars;
        _chunkOverlapChars = _appSettings.ChunkOverlapChars;
        _chunkMaxChars = _appSettings.ChunkMaxChars;
        _llmProvider = _appSettings.LlmProvider;
        // 单例在 App.LoadSettings 已统一解密为明文；此处不再回写单例，
        // 避免运行态明文/落盘密文状态互相污染（曾导致明文落盘与密文被覆盖）
        _llmApiKey = _appSettings.LlmApiKey;
        _llmBaseUrl = _appSettings.LlmBaseUrl;
        _llmModel = _appSettings.LlmModel;
        _llmTemperature = _appSettings.LlmTemperature;
        _llmMaxTokens = _appSettings.LlmMaxTokens;
        _ragTopK = _appSettings.RagTopK;
        _ragSystemPrompt = _appSettings.RagSystemPrompt;
        _ragMaxHistoryTokens = _appSettings.RagMaxHistoryTokens;
        _watchDebounceSeconds = _appSettings.WatchDebounceSeconds;

        WatchPaths.Clear();
        if (_appSettings.WatchPaths != null)
        {
            foreach (var p in _appSettings.WatchPaths)
            {
                if (!string.IsNullOrWhiteSpace(p))
                    WatchPaths.Add(p.Trim());
            }
        }

        _savedApiKeyAtLoad = _llmApiKey;
        _savedBaseUrlAtLoad = _llmBaseUrl;
        _savedModelAtLoad = _llmModel;
        _savedRagSystemPromptAtLoad = _ragSystemPrompt;

        // 智能匹配预设服务商
        _selectedPreset = AvailablePresets.FirstOrDefault(p =>
            p.Id != "custom" &&
            (p.Provider == _llmProvider && !string.IsNullOrWhiteSpace(p.BaseUrl) && _llmBaseUrl != null && _llmBaseUrl.StartsWith(p.BaseUrl, StringComparison.OrdinalIgnoreCase))
        ) ?? (AvailablePresets.FirstOrDefault(p => p.Provider == _llmProvider) ?? AvailablePresets[0]);

        // 密文解密失败（换 Windows 用户/文件损坏）：显式提醒重输，而不是静默当作未配置
        // （静默变空曾让用户改其他参数一保存就把已配置的 Key 永久抹掉）
        if (_appSettings.LlmKeyDecryptFailed)
        {
            StatusMessage = "⚠ 已配置的 API Key 无法解密，请重新输入后保存";
            _notifications.Warning(
                "已配置的 API Key 无法解密（可能更换过 Windows 用户或文件损坏），请在下方重新输入并保存。",
                "API Key");
        }

        // 异步拉取后端实际配置（key 已配置态 / config.toml 损坏告警），回填后刷新 UI。
        // 不阻塞构造；后端不可达/未实现时静默跳过（收尾置空响应）。
        _ = LoadBackendConfigAsync();
        _ = DetectLocalAiAsync();
    }

    // ===================== 本地 AI 环境智能感知 =====================

    private LocalAiEnvironment? _localAiEnv;
    private bool _isDetectingLocalAi;

    public LocalAiEnvironment? LocalAiEnv
    {
        get => _localAiEnv;
        private set
        {
            if (SetProperty(ref _localAiEnv, value))
            {
                OnPropertyChanged(nameof(HasLocalAiDetected));
                OnPropertyChanged(nameof(IsOllamaRunning));
                OnPropertyChanged(nameof(IsLmStudioRunning));
                OnPropertyChanged(nameof(OllamaStatusText));
                OnPropertyChanged(nameof(LmStudioStatusText));
                OnPropertyChanged(nameof(LocalGgufCountText));
            }
        }
    }

    public bool IsDetectingLocalAi
    {
        get => _isDetectingLocalAi;
        private set => SetProperty(ref _isDetectingLocalAi, value);
    }

    public bool HasLocalAiDetected => LocalAiEnv != null && (LocalAiEnv.Ollama.Running || LocalAiEnv.LmStudio.Running || LocalAiEnv.LocalGgufCount > 0);
    public bool IsOllamaRunning => LocalAiEnv?.Ollama?.Running == true;
    public bool IsLmStudioRunning => LocalAiEnv?.LmStudio?.Running == true;
    public string OllamaStatusText => IsOllamaRunning ? "运行中 (已就绪)" : "未启动";
    public string LmStudioStatusText => IsLmStudioRunning ? "运行中 (已就绪)" : "未启动";
    public string LocalGgufCountText => LocalAiEnv?.LocalGgufCount > 0 ? $"已扫描到本地 {LocalAiEnv.LocalGgufCount} 个 GGUF 大模型" : "";

    [RelayCommand]
    public async Task DetectLocalAiAsync()
    {
        IsDetectingLocalAi = true;
        try
        {
            var res = await _apiService.GetLocalAiEnvironmentAsync();
            LocalAiEnv = res;
            DebugLog.Info($"本地 AI 环境探测完成: Ollama={res?.Ollama?.Running} LMStudio={res?.LmStudio?.Running} GGUF={res?.LocalGgufCount}", "Settings");
        }
        catch (Exception ex)
        {
            DebugLog.Debug($"本地 AI 环境探测失败（忽略）: {ex.Message}", "Settings");
        }
        finally
        {
            IsDetectingLocalAi = false;
        }
    }

    [RelayCommand]
    public void ApplyOllamaPreset()
    {
        if (LocalAiEnv?.Ollama == null) return;
        var info = LocalAiEnv.Ollama;
        LlmProvider = "ollama";
        LlmBaseUrl = string.IsNullOrWhiteSpace(info.BaseUrl) ? "http://127.0.0.1:11434" : info.BaseUrl;
        LlmApiKey = "";
        if (!string.IsNullOrWhiteSpace(info.DefaultChatModel))
        {
            LlmModel = info.DefaultChatModel;
        }
        else if (info.Models.Count > 0)
        {
            LlmModel = info.Models[0].Name;
        }
        StatusMessage = "✅ 已一键套用 Ollama 本地最佳方案！";
        _notifications.Success($"已一键绑定 Ollama 本地服务（模型: {LlmModel}）", "智能装配");
        // 自动拉取并测试
        _ = TestConnectionAsync();
        _ = RefreshLlmModelsAsync();
    }

    [RelayCommand]
    public void ApplyLmStudioPreset()
    {
        if (LocalAiEnv?.LmStudio == null) return;
        var info = LocalAiEnv.LmStudio;
        LlmProvider = "openai";
        LlmBaseUrl = string.IsNullOrWhiteSpace(info.BaseUrl) ? "http://127.0.0.1:1234/v1" : info.BaseUrl;
        LlmApiKey = "lm-studio";
        if (!string.IsNullOrWhiteSpace(info.DefaultChatModel))
        {
            LlmModel = info.DefaultChatModel;
        }
        StatusMessage = "✅ 已一键套用 LM Studio 极速本地方案！";
        _notifications.Success($"已一键绑定 LM Studio（模型: {LlmModel ?? "默认"}，RTX 2060 显卡全速加速）", "智能装配");
        // 自动拉取并测试
        _ = TestConnectionAsync();
        _ = RefreshLlmModelsAsync();
    }

    /// <summary>拉取后端 /v1/config 回填运行时真相：API Key 是否已配置（可能由环境变量/
    /// 后端注入，本地 appsettings 未必有）、config.toml 是否损坏。不覆盖用户正在编辑的字段。
    /// internal：测试可直接 await 验证回填行为（构造函数中 fire-and-forget 调用）。</summary>
    internal async Task LoadBackendConfigAsync()
    {
        try
        {
            var cfg = await _apiService.GetConfigAsync();
            if (cfg is null)
            {
                return;
            }
            _backendApiKeyConfigured = cfg.LlmApiKeyConfigured;
            OnPropertyChanged(nameof(HasSavedApiKey));

            if (!string.IsNullOrWhiteSpace(cfg.ConfigError))
            {
                StatusMessage = "⚠ " + cfg.ConfigError;
                DebugLog.Warn($"后端配置告警: {cfg.ConfigError}", "Settings");
            }
            else if (!string.IsNullOrWhiteSpace(cfg.Notice) && string.IsNullOrWhiteSpace(StatusMessage))
            {
                // 仅在没有更紧急状态时透传后端 notice（如换模型后需重建索引提示）
                StatusMessage = cfg.Notice;
                DebugLog.Info($"后端配置提示: {cfg.Notice}", "Settings");
            }
        }
        catch (Exception ex)
        {
            // 后端不可达 / Fake 未实现：静默，不打扰设置页
            DebugLog.Debug($"拉取后端配置失败（忽略）: {ex.GetType().Name}: {ex.Message}", "Settings");
        }
    }

    /// <summary>GPU 加速状态（警告条 + 关于区显示）。</summary>
    public GpuWarningViewModel GpuWarning => _gpuWarning;

    public ThemeMode SelectedTheme
    {
        get => _themeService.CurrentTheme;
        set
        {
            if (value != _themeService.CurrentTheme)
            {
                _themeService.ApplyTheme(value);
                OnPropertyChanged();
                IsDirty = true;
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

    /// <summary>HuggingFace 镜像端点（注入 HF_ENDPOINT 环境变量）；空 = 用内置默认值 hf-mirror.com。</summary>
    public string? HfEndpoint
    {
        get => _hfEndpoint;
        set => SetDirty(ref _hfEndpoint, value);
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

    /// <summary>LLM 提供商标识（none | openai | ollama | anthropic | gemini）。</summary>
    public string LlmProvider
    {
        get => _llmProvider;
        set => SetDirty(ref _llmProvider, value);
    }

    /// <summary>API Key（内存中持明文，落盘时经 DPAPI 加密；留空保存 = 保留原值）。</summary>
    public string? LlmApiKey
    {
        get => _llmApiKey;
        set
        {
            if (SetDirty(ref _llmApiKey, value) && !string.IsNullOrWhiteSpace(value))
            {
                // 重新输入即取消「清除」请求
                _clearApiKeyRequested = false;
            }
        }
    }

    /// <summary>是否已配置 API Key（控制「清除 Key」按钮可用性）。
    /// 本地 appsettings 有 key，或后端报告已配置（环境变量/手动注入）均视为已配置，
    /// 保证「后端有 key 但本地快照没有」时用户仍能清除。</summary>
    public bool HasSavedApiKey => !string.IsNullOrWhiteSpace(_savedApiKeyAtLoad) || _backendApiKeyConfigured;

    /// <summary>API 基础地址（如 https://api.deepseek.com/v1）。</summary>
    public string? LlmBaseUrl
    {
        get => _llmBaseUrl;
        set => SetDirty(ref _llmBaseUrl, value);
    }

    /// <summary>模型名（如 deepseek-chat、gpt-4o-mini、llama3.2）。</summary>
    public string LlmModel
    {
        get => _llmModel;
        set => SetDirty(ref _llmModel, value);
    }

    /// <summary>温度参数（0-2，默认 0.7）。</summary>
    public double LlmTemperature
    {
        get => _llmTemperature;
        set => SetDirty(ref _llmTemperature, value);
    }

    /// <summary>最大 token 数（默认 2048）。</summary>
    public int LlmMaxTokens
    {
        get => _llmMaxTokens;
        set => SetDirty(ref _llmMaxTokens, value);
    }

    /// <summary>检索引用 chunk 数（默认 5）。</summary>
    public int RagTopK
    {
        get => _ragTopK;
        set => SetDirty(ref _ragTopK, value);
    }

    /// <summary>自定义 RAG 系统提示词；空 = 用后端内置默认提示词（基于资料回答+引用来源）。</summary>
    public string? RagSystemPrompt
    {
        get => _ragSystemPrompt;
        set => SetDirty(ref _ragSystemPrompt, value);
    }

    /// <summary>多轮对话历史 token 预算（0 = 不按 token 截断，仍受后端 20 条上限保护）。</summary>
    public int RagMaxHistoryTokens
    {
        get => _ragMaxHistoryTokens;
        set => SetDirty(ref _ragMaxHistoryTokens, value);
    }

    /// <summary>「获取模型列表」拉取到的可用模型（设置页模型下拉候选；仍可手输任意名称）。</summary>
    public System.Collections.ObjectModel.ObservableCollection<string> LlmModels { get; } = new();

    /// <summary>是否正在拉取模型列表。</summary>
    public bool IsFetchingModels
    {
        get => _isFetchingModels;
        set => SetProperty(ref _isFetchingModels, value);
    }

    private readonly List<EmbedModelOption> _embedModelOptions = new()
    {
        new("中文文档 · 推荐（快速省资源）", "BAAI/bge-small-zh-v1.5"),
        new("英文文档 · 快速", "BAAI/bge-small-en-v1.5"),
        new("英文文档 · 均衡", "BAAI/bge-base-en-v1.5"),
        new("英文文档 · 最高精度（较慢）", "BAAI/bge-large-en-v1.5"),
        new("多语言混合 · 快速", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
        new("中英长文档（支持超长文本）", "jinaai/jina-embeddings-v2-base-zh"),
        new("多语言混合 · 最高精度（较慢）", "intfloat/multilingual-e5-large"),
    };

    /// <summary>可选嵌入模型（场景化标签 + 模型名；与后端 catalog 一致，均为 fastembed 实际支持）。</summary>
    public IReadOnlyList<EmbedModelOption> EmbedModelOptions => _embedModelOptions;

    // --- 大模型服务商预设模版 ---
    public IReadOnlyList<LlmPreset> AvailablePresets => LlmPresetCatalog.All;
    private LlmPreset _selectedPreset;

    /// <summary>当前选中的大模型服务商预设。</summary>
    public LlmPreset SelectedPreset
    {
        get => _selectedPreset;
        set
        {
            if (SetProperty(ref _selectedPreset, value ?? AvailablePresets[0]))
            {
                ApplyPreset(_selectedPreset);
                OnPropertyChanged(nameof(SelectedPresetConsoleUrl));
                OnPropertyChanged(nameof(HasPresetConsoleUrl));
            }
        }
    }

    /// <summary>当前选中的服务商控制台/获取 Key 网址。</summary>
    public string? SelectedPresetConsoleUrl => SelectedPreset?.ConsoleUrl;

    /// <summary>当前选中的服务商是否有可直达的控制台网址。</summary>
    public bool HasPresetConsoleUrl => !string.IsNullOrWhiteSpace(SelectedPresetConsoleUrl);

    /// <summary>在浏览器中打开当前服务商的 API Key 申请/管理控制台。</summary>
    [RelayCommand]
    private void OpenPresetConsoleUrl()
    {
        if (string.IsNullOrWhiteSpace(SelectedPresetConsoleUrl))
        {
            return;
        }

        try
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = SelectedPresetConsoleUrl,
                UseShellExecute = true,
            });
            StatusMessage = $"已在浏览器打开【{SelectedPreset.DisplayName}】控制台";
        }
        catch (Exception ex)
        {
            StatusMessage = $"打开浏览器失败: {ex.Message}";
            _notifications.Warning($"无法自动打开浏览器，请手动访问：{SelectedPresetConsoleUrl}", "打开控制台");
        }
    }

    private void ApplyPreset(LlmPreset preset)
    {
        if (preset.Id == "custom")
        {
            return;
        }

        LlmProvider = preset.Provider;
        if (!string.IsNullOrWhiteSpace(preset.BaseUrl))
        {
            LlmBaseUrl = preset.BaseUrl;
        }
        if (!string.IsNullOrWhiteSpace(preset.DefaultModel))
        {
            LlmModel = preset.DefaultModel;
        }

        // 填充推荐模型到下拉框
        LlmModels.Clear();
        foreach (var m in preset.RecommendedModels)
        {
            if (!string.IsNullOrWhiteSpace(m))
            {
                LlmModels.Add(m);
            }
        }

        StatusMessage = $"已应用【{preset.DisplayName}】预设：{preset.Description}";
    }

    // --- 系统体检与自愈诊断 (Doctor) ---
    private DoctorReportResult? _doctorReport;
    private bool _isRunningDoctor;

    /// <summary>系统体检报告。</summary>
    public DoctorReportResult? DoctorReport
    {
        get => _doctorReport;
        set
        {
            if (SetProperty(ref _doctorReport, value))
            {
                OnPropertyChanged(nameof(HasDoctorReport));
                OnPropertyChanged(nameof(DoctorScoreText));
            }
        }
    }

    public bool HasDoctorReport => DoctorReport is not null;
    public string DoctorScoreText => DoctorReport is not null ? $"{DoctorReport.Score} / 100" : "-";

    /// <summary>是否正在执行系统体检。</summary>
    public bool IsRunningDoctor
    {
        get => _isRunningDoctor;
        set => SetProperty(ref _isRunningDoctor, value);
    }

    /// <summary>执行系统全面体检与自愈诊断命令。</summary>
    [RelayCommand]
    public async Task RunDoctorAsync()
    {
        if (IsRunningDoctor)
            return;

        IsRunningDoctor = true;
        StatusMessage = "正在执行系统全面体检...";
        DebugLog.Info("开始执行系统体检 (Doctor)", "Settings");

        try
        {
            var report = await _apiService.GetDoctorReportAsync(network: true);
            DoctorReport = report;
            StatusMessage = $"系统体检完成 (得分: {report.Score}) · {report.Summary}";
            DebugLog.Info($"系统体检完成: status={report.OverallStatus} score={report.Score}", "Settings");
            _notifications.Success($"系统体检完成！健康评分: {report.Score}/100\n{report.Summary}", "体检报告");
        }
        catch (Exception ex)
        {
            StatusMessage = $"❌ 系统体检失败: {ex.Message}";
            DebugLog.Error($"系统体检异常: {ex.Message}", "Settings", ex);
            _notifications.Error($"系统体检失败：{ex.Message}");
        }
        finally
        {
            IsRunningDoctor = false;
        }
    }

    /// <summary>底部状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>是否正在测试连接。</summary>
    public bool IsTestingConnection
    {
        get => _isTestingConnection;
        set => SetProperty(ref _isTestingConnection, value);
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
            // key 语义（与 UI ToolTip 承诺一致）：
            //   非空      → 使用输入值；
            //   留空      → 保留原值（本地密文不覆盖、后端不修改）；
            //   点了「清除」→ 本地置空 + 后端显式清除。
            var hasApiKeyInput = !string.IsNullOrWhiteSpace(LlmApiKey);
            
            if (_appSettings.LlmKeyDecryptFailed && !hasApiKeyInput && !_clearApiKeyRequested)
            {
                _notifications.Error("API 密钥解密失败，为防止原密钥丢失，请重新输入密钥或点击清除后再保存。", "需要操作");
                StatusMessage = "保存中止：需要处理 API 密钥";
                return;
            }

            var effectiveApiKey = hasApiKeyInput
                ? LlmApiKey!.Trim()
                : (_clearApiKeyRequested ? null : _appSettings.LlmApiKey);

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
            _appSettings.HfEndpoint = HfEndpoint;
            _appSettings.ChunkMaxTokens = ChunkMaxTokens;
            _appSettings.ChunkMinChars = ChunkMinChars;
            _appSettings.ChunkOverlapChars = ChunkOverlapChars;
            _appSettings.ChunkMaxChars = ChunkMaxChars;
            _appSettings.LlmProvider = LlmProvider;
            _appSettings.LlmApiKey = effectiveApiKey;
            _appSettings.LlmBaseUrl = LlmBaseUrl;
            _appSettings.LlmModel = LlmModel;
            _appSettings.LlmTemperature = LlmTemperature;
            _appSettings.LlmMaxTokens = LlmMaxTokens;
            _appSettings.RagTopK = RagTopK;
            _appSettings.RagSystemPrompt = string.IsNullOrWhiteSpace(RagSystemPrompt) ? null : RagSystemPrompt;
            _appSettings.RagMaxHistoryTokens = RagMaxHistoryTokens;
            _appSettings.WatchPaths = WatchPaths.Where(p => !string.IsNullOrWhiteSpace(p)).Select(p => p.Trim()).ToList();
            _appSettings.WatchDebounceSeconds = WatchDebounceSeconds;
            _appSettings.DismissGpuWarning = _gpuWarning.Dismissed;

            // 落盘：AppSettings.Save() 是唯一持久化出口（全字段 camelCase，key 经 DPAPI 加密），
            // 避免双写盘路径（匿名对象/全对象、PascalCase/camelCase）互相覆盖。
            // 内存/AppSettings 单例仍持明文，供 BackendProcessService 注入环境变量与测试连接使用。
            _appSettings.Save();
            var settingsPath = AppSettings.ConfigPath;

            // 推送到后端运行时配置（/v1/config），免重启生效；
            // 推送失败会显式警告（重启后端后环境变量仍会生效，见 BackendProcessService）。
            var pushFailed = false;
            try
            {
                // key：非空推明文；留空推 null（不修改后端已配置值）；「清除」推 "" 显式清除
                string? pushApiKey = hasApiKeyInput
                    ? LlmApiKey!.Trim()
                    : (_clearApiKeyRequested ? "" : null);
                // base_url 清除语义：之前配置过、现在被清空 → 传 "" 显式清除；
                // 之前就没配置 → 传 null 不修改（避免误清后端手动配置的值）
                var pushBaseUrl = string.IsNullOrWhiteSpace(LlmBaseUrl)
                    ? (string.IsNullOrWhiteSpace(_savedBaseUrlAtLoad) ? null : "")
                    : LlmBaseUrl.Trim();

                var pushed = await _apiService.UpdateConfigAsync(new BackendConfigUpdate
                {
                    // 空模型名不推送，避免清空后端配置
                    EmbedModel = string.IsNullOrWhiteSpace(EmbedModel) ? null : EmbedModel.Trim(),
                    // 本地模型目录（空字符串 = 清除本地模型，回到联网模型）
                    EmbedModelPath = string.IsNullOrWhiteSpace(EmbedModelPath) 
                        ? (string.IsNullOrWhiteSpace(_appSettings.EmbedModelPath) ? null : "") 
                        : EmbedModelPath.Trim(),
                    EmbedBatchSize = null, // 前端暂不暴露
                    ChunkMaxTokens = ChunkMaxTokens,
                    ChunkMinChars = ChunkMinChars,
                    ChunkOverlapChars = ChunkOverlapChars,
                    ChunkMaxChars = ChunkMaxChars,
                    SearchTopK = null,     // 前端暂不暴露
                    RrfK = null,           // 前端暂不暴露
                    LlmProvider = string.IsNullOrWhiteSpace(LlmProvider) ? "none" : LlmProvider,
                    LlmApiKey = pushApiKey,
                    LlmBaseUrl = pushBaseUrl,
                    LlmModel = string.IsNullOrWhiteSpace(LlmModel)
                        ? (string.IsNullOrWhiteSpace(_savedModelAtLoad) ? null : "")
                        : LlmModel.Trim(),
                    LlmTemperature = LlmTemperature,
                    LlmMaxTokens = LlmMaxTokens,
                    RagTopK = RagTopK,
                    RagSystemPrompt = string.IsNullOrWhiteSpace(RagSystemPrompt)
                        ? (string.IsNullOrWhiteSpace(_savedRagSystemPromptAtLoad) ? null : "")
                        : RagSystemPrompt.Trim(),
                    RagMaxHistoryTokens = RagMaxHistoryTokens,
                    WatchPaths = WatchPaths.Where(p => !string.IsNullOrWhiteSpace(p)).Select(p => p.Trim()).ToList(),
                    WatchDebounceSeconds = WatchDebounceSeconds,
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
                pushFailed = true;
                DebugLog.Warn($"后端参数推送失败（重启后端后仍会生效）: {ex.Message}", "Settings");
                _notifications.Warning(
                    $"后端推送失败：{ex.Message}\n配置已保存到本地，重启后端后将通过环境变量生效。",
                    "配置未即时同步");
            }

            IsDirty = false;
            _clearApiKeyRequested = false;
            _savedApiKeyAtLoad = effectiveApiKey;
            _savedBaseUrlAtLoad = LlmBaseUrl;
            _savedModelAtLoad = LlmModel;
            _savedRagSystemPromptAtLoad = RagSystemPrompt;
            // 保存后 key 已配置态与本次推送对齐：推了新 key → 已配置；清除（推 ""）→ 未配置；
            // 留空保留 → 维持后端此前状态（可能是环境变量注入的 key）
            if (hasApiKeyInput)
            {
                _backendApiKeyConfigured = true;
            }
            else if (_clearApiKeyRequested)
            {
                _backendApiKeyConfigured = false;
            }
            OnPropertyChanged(nameof(HasSavedApiKey));
            var keyNote = !hasApiKeyInput && !_clearApiKeyRequested && !string.IsNullOrWhiteSpace(_savedApiKeyAtLoad)
                ? "；API Key 保留原值"
                : "";
            StatusMessage = pushFailed
                ? $"已保存（后端推送失败，重启后端后生效）{keyNote}"
                : $"已保存（模型/分块参数已实时生效；其余变更重启后端生效）{keyNote}";
            if (!pushFailed)
            {
                _notifications.Success("设置已保存");
            }
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
        HfEndpoint = _appSettings.HfEndpoint;
        ChunkMaxTokens = _appSettings.ChunkMaxTokens;
        ChunkMinChars = _appSettings.ChunkMinChars;
        ChunkOverlapChars = _appSettings.ChunkOverlapChars;
        ChunkMaxChars = _appSettings.ChunkMaxChars;
        LlmProvider = _appSettings.LlmProvider;
        LlmApiKey = _appSettings.LlmApiKey;
        LlmBaseUrl = _appSettings.LlmBaseUrl;
        LlmModel = _appSettings.LlmModel;
        LlmTemperature = _appSettings.LlmTemperature;
        LlmMaxTokens = _appSettings.LlmMaxTokens;
        RagTopK = _appSettings.RagTopK;
        RagSystemPrompt = _appSettings.RagSystemPrompt;
        RagMaxHistoryTokens = _appSettings.RagMaxHistoryTokens;
        
        WatchPaths.Clear();
        foreach (var p in _appSettings.WatchPaths)
        {
            WatchPaths.Add(p);
        }
        WatchDebounceSeconds = _appSettings.WatchDebounceSeconds;
        
        var theme = _appSettings.Theme == "Dark" ? ThemeMode.Dark : ThemeMode.Light;
        if (SelectedTheme != theme)
        {
            SelectedTheme = theme;
        }

        _clearApiKeyRequested = false; // 恢复未保存的改动，包括未保存的「清除」请求
        IsDirty = false;
        StatusMessage = "已恢复";
    }

    /// <summary>显式清除已配置的 API Key（保存时本地置空并向后端推送清除）。
    /// key 输入框留空保存只会保留原值，不会清除——清除必须走此按钮。</summary>
    [RelayCommand]
    private void ClearApiKey()
    {
        _clearApiKeyRequested = true;
        LlmApiKey = null;
        // 即使本地本就无 key（值未变化，setter 不会标记 dirty），清除请求本身
        // 也要进入保存流程：后端可能有 key（环境变量/手动配置），此时仍要推送
        // "" 显式清除。若没有这行，dirty=false 时保存按钮禁用，清除永远无法生效。
        IsDirty = true;
        SaveCommand.NotifyCanExecuteChanged();
    }

    [RelayCommand]
    private void SetLightTheme() => SelectedTheme = ThemeMode.Light;

    [RelayCommand]
    private void SetDarkTheme() => SelectedTheme = ThemeMode.Dark;

    /// <summary>测试后端可达性与 LLM 连接。
    /// LLM 测试用 UI 当前输入值（未保存也能测）——字段留空时后端沿用当前运行时配置。</summary>
    [RelayCommand]
    private async Task TestConnectionAsync()
    {
        if (IsTestingConnection)
            return;

        IsTestingConnection = true;
        StatusMessage = "测试连接中…";
        DebugLog.Info("开始测试连接", "Settings");

        try
        {
            // 1. 测试后端可达性
            var health = await _apiService.GetHealthAsync();
            if (health is null)
            {
                StatusMessage = "❌ 后端不可达";
                DebugLog.Warn("测试连接失败: 后端不可达", "Settings");
                return;
            }

            // 2. 未选择提供商：只测后端
            if (string.IsNullOrWhiteSpace(LlmProvider) || LlmProvider == "none")
            {
                StatusMessage = "✅ 后端连接正常（未选择 LLM 提供商，跳过对话测试）";
                DebugLog.Info("测试连接成功（未配置 LLM）", "Settings");
                return;
            }

            // 3. 用 UI 当前输入值测 LLM（POST /v1/llm/test，无需先保存；
            //    key/base_url/model 留空时后端沿用当前运行时配置）
            var result = await _apiService.LlmTestAsync(new LlmTestRequest
            {
                Provider = LlmProvider.Trim(),
                ApiKey = string.IsNullOrWhiteSpace(LlmApiKey) ? null : LlmApiKey.Trim(),
                BaseUrl = string.IsNullOrWhiteSpace(LlmBaseUrl) ? null : LlmBaseUrl.Trim(),
                Model = string.IsNullOrWhiteSpace(LlmModel) ? null : LlmModel.Trim(),
                Timeout = 20,
            });

            if (result.Ok)
            {
                StatusMessage = $"✅ LLM 连接成功 · {result.Provider} / {result.Model} · {result.ElapsedMs}ms"
                    + (string.IsNullOrEmpty(result.ReplyPreview) ? "" : $" · 回复: {result.ReplyPreview}");
                DebugLog.Info($"LLM 测试成功: provider={result.Provider} model={result.Model} {result.ElapsedMs}ms", "Settings");
            }
            else
            {
                StatusMessage = $"❌ LLM 测试失败 · {result.Provider}: {result.Error ?? "未知错误"}";
                DebugLog.Warn($"LLM 测试失败: {result.Error}", "Settings");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"❌ 连接失败: {ex.Message}";
            DebugLog.Error($"测试连接异常: {ex.Message}", "Settings", ex);
        }
        finally
        {
            IsTestingConnection = false;
        }
    }

    /// <summary>拉取当前提供商的可用模型列表（POST /v1/llm/models）。
    /// 用 UI 当前输入值（无需先保存）：Ollama 列本地已装模型，云端调各家 /models 接口；
    /// key/base_url 留空时后端沿用当前运行时配置。失败不打断（仍可手输模型名）。</summary>
    [RelayCommand]
    private async Task RefreshLlmModelsAsync()
    {
        if (IsFetchingModels)
            return;

        if (string.IsNullOrWhiteSpace(LlmProvider) || LlmProvider == "none")
        {
            StatusMessage = "❌ 请先选择 LLM 提供商，再获取模型列表";
            return;
        }

        IsFetchingModels = true;
        StatusMessage = "获取模型列表中…";
        DebugLog.Info($"开始获取模型列表: provider={LlmProvider}", "Settings");

        try
        {
            var result = await _apiService.LlmModelsAsync(new LlmModelsRequest
            {
                Provider = LlmProvider.Trim(),
                ApiKey = string.IsNullOrWhiteSpace(LlmApiKey) ? null : LlmApiKey.Trim(),
                BaseUrl = string.IsNullOrWhiteSpace(LlmBaseUrl) ? null : LlmBaseUrl.Trim(),
                Timeout = 10,
            });

            if (result.Ok)
            {
                LlmModels.Clear();
                foreach (var m in result.Models)
                {
                    LlmModels.Add(m);
                }
                StatusMessage = $"✅ 获取到 {result.Models.Count} 个模型（{result.Provider}）";
                DebugLog.Info($"模型列表获取成功: provider={result.Provider} count={result.Models.Count}", "Settings");
            }
            else
            {
                StatusMessage = $"❌ 获取模型列表失败: {result.Error ?? "未知错误"}";
                DebugLog.Warn($"模型列表获取失败: {result.Error}", "Settings");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"❌ 获取模型列表失败: {ex.Message}";
            DebugLog.Error($"获取模型列表异常: {ex.Message}", "Settings", ex);
        }
        finally
        {
            IsFetchingModels = false;
        }
    }

    // ===================== 文件监控 =====================

    private string _newWatchPath = "";
    private string? _selectedWatchPath;
    private double _watchDebounceSeconds = 5.0;

    public System.Collections.ObjectModel.ObservableCollection<string> WatchPaths { get; } = new();

    public string NewWatchPath
    {
        get => _newWatchPath;
        set => SetProperty(ref _newWatchPath, value);
    }

    public string? SelectedWatchPath
    {
        get => _selectedWatchPath;
        set => SetProperty(ref _selectedWatchPath, value);
    }

    public double WatchDebounceSeconds
    {
        get => _watchDebounceSeconds;
        set => SetDirty(ref _watchDebounceSeconds, value);
    }

    [RelayCommand]
    private void AddWatchPath()
    {
        if (string.IsNullOrWhiteSpace(NewWatchPath)) return;
        var p = NewWatchPath.Trim();
        if (!WatchPaths.Contains(p))
        {
            WatchPaths.Add(p);
            IsDirty = true;
        }
        NewWatchPath = "";
    }

    [RelayCommand]
    private void RemoveWatchPath(string? path)
    {
        var target = path ?? SelectedWatchPath;
        if (!string.IsNullOrWhiteSpace(target) && WatchPaths.Remove(target))
        {
            IsDirty = true;
        }
    }

    [RelayCommand]
    private void BrowseWatchPath()
    {
        var dialog = new Microsoft.Win32.OpenFolderDialog
        {
            Title = "选择要监控的文档目录",
            Multiselect = false
        };
        if (dialog.ShowDialog() == true)
        {
            NewWatchPath = dialog.FolderName;
        }
    }

    [RelayCommand]
    public async Task RestartBackendAsync()
    {
        if (_backendProcess == null)
        {
            StatusMessage = "未配置后端管理服务";
            return;
        }

        StatusMessage = "正在强力重启后端服务...";
        _notifications.Info("正在强力重启后端服务...", "重启中");
        try
        {
            var ok = await _backendProcess.RestartAsync();
            if (ok)
            {
                StatusMessage = "✅ 后端服务已成功重启并对齐最新端口";
                _notifications.Success("后端服务已重启，端口与实例已对齐！", "重启成功");
            }
            else
            {
                StatusMessage = "❌ 后端服务重启失败，请查看日志";
                _notifications.Error("后端服务重启失败", "错误");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"❌ 重启异常: {ex.Message}";
            _notifications.Error($"重启异常: {ex.Message}", "错误");
        }
    }
}

/// <summary>嵌入模型下拉展示项：场景化标签（面向普通用户）+ 实际模型名（传后端 DOC2MIND_EMBED_MODEL）。</summary>
public sealed record EmbedModelOption(string Label, string ModelId)
{
    public override string ToString() => Label;
}
