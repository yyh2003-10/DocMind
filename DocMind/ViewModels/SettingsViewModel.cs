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
    /// <summary>服务商配置变更事件（新增/更新/删除/启停/设默认后触发）。
    /// ChatViewModel 订阅后立即重建模型候选，实现设置页改完对话页立即可点选（修复 LobeChat 同类坑）。</summary>
    public static event Action? ProviderConfigChanged;

    /// <summary>触发服务商配置变更通知（各写操作成功后调用）。</summary>
    private static void RaiseProviderConfigChanged() => ProviderConfigChanged?.Invoke();

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
            // 首次进入「算力与体检」时静默拉取运行依赖状态（二次进入跳过，手动刷新按钮兜底）
            if (cat == SettingsCategory.Hardware && Dependencies is null && !IsDependenciesLoading)
            {
                _ = RefreshDependenciesAsync();
            }
        }
    }

    private string _backendUrl;
    private int _pollIntervalMs;
    private int _startupTimeoutSec;
    private string? _backendCommand;
    private bool _autoStartBackend = true;
    private bool _stopBackendOnExit = true;
    private bool _autoCurateOnIngest = true;
    private bool _agentModeEnabled;
    private string _defaultChatMode = "rag";
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
    private int _llmMaxTokens = 8192;
    private double _llmTimeoutSec = 300;
    private double _webSearchTimeoutSec = 36;
    private string? _webSearchSearxngUrl;
    private int _ragTopK = 5;
    private string? _ragSystemPrompt;
    private int _ragMaxHistoryTokens = 4096;
    private string _ragMode = "hybrid";
    private string _usageProfile = "docs";
    private string _pendingUsageProfile = "docs";
    private bool _rerankEnabled = true;

    /// <summary>已应用的使用档案（与后端一致）。</summary>
    public string UsageProfile
    {
        get => _usageProfile;
        private set
        {
            if (SetProperty(ref _usageProfile, value))
            {
                OnPropertyChanged(nameof(UsageProfileDescription));
                OnPropertyChanged(nameof(HasPendingProfileChange));
            }
        }
    }

    /// <summary>下拉框当前选中（可能尚未应用）。</summary>
    public string PendingUsageProfile
    {
        get => _pendingUsageProfile;
        set
        {
            if (SetProperty(ref _pendingUsageProfile, value))
            {
                OnPropertyChanged(nameof(PendingUsageProfileDescription));
                OnPropertyChanged(nameof(HasPendingProfileChange));
            }
        }
    }

    public bool HasPendingProfileChange =>
        !string.Equals(PendingUsageProfile, UsageProfile, StringComparison.OrdinalIgnoreCase);

    /// <summary>当前档案的一句话说明。</summary>
    public string UsageProfileDescription => ProfileLabel(UsageProfile);

    public string PendingUsageProfileDescription => ProfileLabel(PendingUsageProfile);

    private static string ProfileLabel(string? profile) => profile switch
    {
        "notes" => "个人沉淀：短答、诚实引用，不硬凑架构洞察与行动列表",
        "agent" => "Agent 记忆：稳定事实 + 编号，少闲聊，适合 MCP 工具调用",
        "library" => "库管理：批量入库与整理优先，对话偏清单步骤",
        _ => "项目文档：参数/流程可核对，强调出处",
    };

    /// <summary>可选档案列表（供下拉）。</summary>
    public IReadOnlyList<KeyValuePair<string, string>> UsageProfileOptions { get; } = new List<KeyValuePair<string, string>>
    {
        new("docs", "项目文档"),
        new("notes", "个人沉淀"),
        new("agent", "Agent 记忆"),
        new("library", "库管理"),
    };

    private bool _isSwitchingProfile;

    [RelayCommand]
    private async Task SwitchUsageProfileAsync(string? profile)
    {
        if (string.IsNullOrWhiteSpace(profile) || _isSwitchingProfile)
        {
            return;
        }

        var target = profile.Trim().ToLowerInvariant();
        if (string.Equals(target, UsageProfile, StringComparison.OrdinalIgnoreCase))
        {
            _notifications.Info("已是当前档案，无需切换", "使用档案");
            return;
        }

        // 二次确认：切换会覆盖手调过的 rag_top_k / rag_mode 等参数
        var label = UsageProfileOptions.FirstOrDefault(p =>
            string.Equals(p.Key, target, StringComparison.OrdinalIgnoreCase)).Value;
        var confirm = System.Windows.MessageBox.Show(
            $"切换到「{label ?? target}」会覆盖当前的检索默认（Top-K、问答模式等）。\n\n" +
            $"当前：{UsageProfileDescription}\n" +
            "确定继续吗？",
            "确认切换使用档案",
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Question);
        if (confirm != System.Windows.MessageBoxResult.Yes)
        {
            // 用户取消：下拉回弹到已应用档案
            PendingUsageProfile = UsageProfile;
            return;
        }

        _isSwitchingProfile = true;
        try
        {
            var result = await _apiService.SetUsageProfileAsync(target);
            UsageProfile = result.Profile;
            PendingUsageProfile = result.Profile;
            if (result.Applied is { } applied)
            {
                RagTopK = applied.RagTopK;
                RagMode = applied.RagMode;
            }
            _appSettings.UsageProfile = UsageProfile;
            if (result.Applied is { } a)
            {
                _appSettings.RagTopK = a.RagTopK;
                _appSettings.RagMode = a.RagMode;
            }
            IsDirty = false;
            _notifications.Success(
                $"已切换到「{result.Preset?.Label ?? target}」：{result.Preset?.Description ?? UsageProfileDescription}");
        }
        catch (Exception ex)
        {
            _notifications.Error($"切换使用档案失败：{ex.Message}");
            PendingUsageProfile = UsageProfile;
        }
        finally
        {
            _isSwitchingProfile = false;
        }
    }
    private string _rerankModel = "BAAI/bge-reranker-base";
    private int _rerankRecall = 20;
    private bool _showRestartBanner;
    private string _restartBannerText = "";
    private string _statusMessage = "就绪";
    private bool _isDirty;
    private bool _isTestingConnection;
    private bool _isFetchingModels;
    private bool _isTestingModels;
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

    // ── 批次 2：配置状态透明化 ──
    // 后端 /v1/config 返回的实际生效配置（状态卡展示用）
    private string? _activeBackendEmbedModel;
    private string? _activeBackendLlmProvider;
    private string? _activeBackendLlmModel;
    private readonly UserMemoryService? _userMemory;
    private string _memoryStatsText = "尚未加载";
    private string _costStatsText = "尚未加载";

    /// <summary>用户记忆统计文案。</summary>
    public string MemoryStatsText
    {
        get => _memoryStatsText;
        private set => SetProperty(ref _memoryStatsText, value);
    }

    /// <summary>LLM 费用统计文案。</summary>
    public string CostStatsText
    {
        get => _costStatsText;
        private set => SetProperty(ref _costStatsText, value);
    }

    private System.Collections.ObjectModel.ObservableCollection<DocMind.Models.MemoryEntry> _memoryEntries = new();
    private DocMind.Models.MemoryEntry? _selectedMemoryEntry;
    private string _memoryEditDraft = "";

    /// <summary>记忆条目列表。</summary>
    public System.Collections.ObjectModel.ObservableCollection<DocMind.Models.MemoryEntry> MemoryEntries
    {
        get => _memoryEntries;
        private set => SetProperty(ref _memoryEntries, value);
    }

    /// <summary>当前选中的记忆。</summary>
    public DocMind.Models.MemoryEntry? SelectedMemoryEntry
    {
        get => _selectedMemoryEntry;
        set
        {
            if (SetProperty(ref _selectedMemoryEntry, value))
            {
                MemoryEditDraft = value?.Content ?? "";
                DeleteMemoryCommand.NotifyCanExecuteChanged();
                SaveMemoryEditCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>记忆编辑草稿。</summary>
    public string MemoryEditDraft
    {
        get => _memoryEditDraft;
        set
        {
            if (SetProperty(ref _memoryEditDraft, value ?? ""))
                SaveMemoryEditCommand.NotifyCanExecuteChanged();
        }
    }

    /// <summary>加载记忆列表。</summary>
    [CommunityToolkit.Mvvm.Input.RelayCommand]
    private async Task LoadMemoriesAsync()
    {
        if (_userMemory is null) return;
        try
        {
            var list = await System.Threading.Tasks.Task.Run(() => _userMemory.GetAllAsync());
            MemoryEntries = new System.Collections.ObjectModel.ObservableCollection<DocMind.Models.MemoryEntry>(list);
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"加载记忆列表失败: {ex.Message}", "Memory");
        }
    }

    /// <summary>保存记忆编辑。</summary>
    [CommunityToolkit.Mvvm.Input.RelayCommand(CanExecute = nameof(CanSaveMemoryEdit))]
    private async Task SaveMemoryEditAsync()
    {
        var entry = SelectedMemoryEntry;
        if (_userMemory is null || entry is null) return;
        var draft = MemoryEditDraft?.Trim() ?? "";
        if (draft.Length == 0 || draft == entry.Content) return;
        try
        {
            await System.Threading.Tasks.Task.Run(() => _userMemory.ReplaceAsync(entry.Content, draft, entry.Category));
            await LoadMemoriesAsync();
            _notifications?.Success("记忆已更新", "记忆");
        }
        catch (Exception ex)
        {
            _notifications?.Error($"保存记忆失败：{ex.Message}", "记忆");
        }
    }

    private bool CanSaveMemoryEdit =>
        _userMemory is not null && SelectedMemoryEntry is not null
        && !string.IsNullOrWhiteSpace(MemoryEditDraft)
        && MemoryEditDraft.Trim() != SelectedMemoryEntry.Content;

    /// <summary>删除选中记忆。</summary>
    [CommunityToolkit.Mvvm.Input.RelayCommand(CanExecute = nameof(CanDeleteMemory))]
    private async Task DeleteMemoryAsync()
    {
        var entry = SelectedMemoryEntry;
        if (_userMemory is null || entry is null) return;
        try
        {
            await System.Threading.Tasks.Task.Run(() => _userMemory.DeleteByIdAsync(entry.Id));
            SelectedMemoryEntry = null;
            await LoadMemoriesAsync();
            _notifications?.Success("记忆已删除", "记忆");
        }
        catch (Exception ex)
        {
            _notifications?.Error($"删除记忆失败：{ex.Message}", "记忆");
        }
    }

    private bool CanDeleteMemory => _userMemory is not null && SelectedMemoryEntry is not null;

    /// <summary>刷新记忆与费用面板。</summary>
    [CommunityToolkit.Mvvm.Input.RelayCommand]
    private async Task RefreshUsageStatsAsync()
    {
        try
        {
            if (_userMemory is not null)
            {
                // SQLite 同步 API：必须进线程池，避免 await 同步完成时卡死 UI 线程
                var mem = await System.Threading.Tasks.Task.Run(() => _userMemory.GetStatsAsync());
                var memories = await System.Threading.Tasks.Task.Run(() => _userMemory.GetAllAsync());
                MemoryEntries = new System.Collections.ObjectModel.ObservableCollection<DocMind.Models.MemoryEntry>(memories);
                MemoryStatsText = $"记忆 {mem.EntryCount} 条 · 已用 {mem.UsedChars}/{mem.MaxChars} 字符（{mem.UsagePercent:F0}%）"
                    + (mem.IsNearLimit ? " · 接近容量上限" : "");
            }
            else
            {
                MemoryStatsText = "记忆服务未注入";
            }
        }
        catch (Exception ex)
        {
            MemoryStatsText = $"记忆统计失败：{ex.Message}";
        }

        try
        {
            if (_costTracker is not null)
            {
                var today = await System.Threading.Tasks.Task.Run(() => _costTracker.GetTodayCostAsync());
                var month = await System.Threading.Tasks.Task.Run(() => _costTracker.GetMonthCostAsync());
                CostStatsText = $"今日  · 本月 ";
            }
            else
            {
                CostStatsText = "费用服务未注入";
            }
        }
        catch (Exception ex)
        {
            CostStatsText = $"费用统计失败：{ex.Message}";
        }
    }
    private readonly CostTracker? _costTracker;

    private bool _isBackendConfigLoaded;
    private bool _showApiKeyClearConfirm;

    // GitHub Token（可选）：联网搜索 GitHub 通道按请求携带（每用户自己的 Token）。
    // 与 LLM API Key 同语义：留空保存 = 保留原值，点「清除」才删除。
    private string? _githubToken;
    private string? _savedGithubTokenAtLoad;
    private bool _clearGithubTokenRequested;

    // ── LLM 连接字段「输入即生效」：提供商/Key/地址/模型变化后防抖推送后端运行时 ──
    // persist=false 不落盘（保存才持久化）；实现输入即可测试、调用，无需先点保存。
    private System.Windows.Threading.DispatcherTimer? _llmAutoApplyTimer;
    private bool _isAutoApplyingLlm;
    private bool _llmAutoApplyPending;
    private long _llmModelsRequestVersion;
    // 表单回填中（下拉选服务商/应用档案前回填）：抑制自动推送（应用档案走完整保存流程自带推送）
    private bool _isBackfillingLlmForm;
    // 上次已推送后端运行时的 LLM 连接快照（避免重复推送）
    private string? _appliedLlmProvider;
    private string? _appliedLlmApiKey;
    private string? _appliedLlmBaseUrl;
    private string? _appliedLlmModel;

    // --- AI 提供商档案 ---
    private LlmProfile? _selectedProfile;
    private string _profileNameInput = "";
    private bool _isApplyingProfile;
    private bool _isCheckingProfiles;
    // 重建服务商下拉期间抑制「选中即应用」副作用（构造初始化/增删档案时只高亮，不触发 ApplyPreset）
    private bool _isRebuildingProviderOptions;
    // 构造函数期间抑制 IsDirty/PropertyChanged/Timer 副作用，避免 WPF 布局阶段属性链递归导致 StackOverflow。
    private bool _isInitializing;

    public SettingsViewModel(
        AppSettings appSettings,
        NotificationService notifications,
        ThemeService themeService,
        IDoc2kbApiService apiService,
        GpuWarningViewModel gpuWarning,
        BackendProcessService? backendProcess = null,
        ResourcePathPanelViewModel? resourcePaths = null,
        UserMemoryService? userMemory = null,
        CostTracker? costTracker = null)
    {
        _isInitializing = true;
        _appSettings = appSettings;
        _userMemory = userMemory;
        _costTracker = costTracker;
        _notifications = notifications;
        _themeService = themeService;
        _apiService = apiService;
        _gpuWarning = gpuWarning;
        _backendProcess = backendProcess;
        ResourcePaths = resourcePaths;
        Title = "设置";

        LlmModels.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HasModels));

        // 异步预检外部组件路径（高概率区快扫，秒级；不阻塞设置页打开）
        if (resourcePaths is not null)
        {
            _ = resourcePaths.RefreshAsync();
        }

        _ = RefreshUsageStatsAsync();

        // 自定义搭配默认选中当前/轻量项，便于用户混搭
        // 默认「平衡」预设：高效简便定位（初始化不标脏）
        ApplyPresetInternal("balanced", markDirty: false);
        
        _gpuWarning.PropertyChanged += (s, e) =>
        {
            // 故意保留：Dismissed 变化在设置页打开时不再直接标记 IsDirty，
            // 避免 WPF 布局/测量阶段属性链递归导致 StackOverflow。
            // 若以后确需关联，请改为仅记录日志并同步设置页相关状态，勿在 setter 中触发 SaveCommand 相关逻辑。
            DebugLog.Debug($"GpuWarning.PropertyChanged: {e.PropertyName ?? "(null)"}", "Settings");
        };

        // 加载当前值到可编辑字段
        _backendUrl = _appSettings.BackendUrl;
        _pollIntervalMs = _appSettings.PollIntervalMs;
        _startupTimeoutSec = _appSettings.StartupTimeoutSec;
        _backendCommand = _appSettings.BackendCommand;
        _autoStartBackend = _appSettings.AutoStartBackend;
        _stopBackendOnExit = _appSettings.StopBackendOnExit;
        _autoCurateOnIngest = _appSettings.AutoCurateOnIngest;
        _agentModeEnabled = _appSettings.AgentModeEnabled;
        _longformEnabled = _appSettings.LongformEnabled;
        _longformMaxSections = _appSettings.LongformMaxSections is >= 2 and <= 16 ? _appSettings.LongformMaxSections : 8;
        _longformMaxChars = _appSettings.LongformMaxChars is >= 1000 and <= 100000 ? _appSettings.LongformMaxChars : 12000;
        _searchProvider = string.IsNullOrWhiteSpace(_appSettings.SearchProvider) ? "builtin" : _appSettings.SearchProvider;
        _searchProviderApiKey = _appSettings.SearchProviderApiKey ?? "";
        _searchProviderEndpoint = _appSettings.SearchProviderEndpoint;
        _defaultChatMode = string.IsNullOrWhiteSpace(_appSettings.DefaultChatMode)
            ? "rag"
            : _appSettings.DefaultChatMode.Trim().ToLowerInvariant();
        if (_defaultChatMode is not ("rag" or "agent" or "auto"))
        {
            _defaultChatMode = "rag";
        }
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
        _githubToken = _appSettings.GithubToken;
        _llmBaseUrl = _appSettings.LlmBaseUrl;
        _llmModel = _appSettings.LlmModel;
        foreach (var model in _appSettings.LlmAvailableModels ?? new List<string>())
        {
            if (!string.IsNullOrWhiteSpace(model) && !LlmModels.Any(m => string.Equals(m.Name, model.Trim(), StringComparison.OrdinalIgnoreCase)))
                LlmModels.Add(new LlmModelItem(model.Trim()));
        }
        _llmTemperature = _appSettings.LlmTemperature;
        _llmMaxTokens = _appSettings.LlmMaxTokens;
        _llmTimeoutSec = _appSettings.LlmTimeoutSec > 0 ? _appSettings.LlmTimeoutSec : 300;
        _webSearchTimeoutSec = _appSettings.WebSearchTimeoutSec > 0 ? _appSettings.WebSearchTimeoutSec : 36;
        _webSearchSearxngUrl = _appSettings.WebSearchSearxngUrl;
        _ragTopK = _appSettings.RagTopK;
        _usageProfile = string.IsNullOrWhiteSpace(_appSettings.UsageProfile)
            ? "docs"
            : _appSettings.UsageProfile.Trim().ToLowerInvariant();
        _pendingUsageProfile = _usageProfile;
        OnPropertyChanged(nameof(UsageProfile));
        OnPropertyChanged(nameof(PendingUsageProfile));
        OnPropertyChanged(nameof(UsageProfileDescription));
        OnPropertyChanged(nameof(PendingUsageProfileDescription));
        OnPropertyChanged(nameof(HasPendingProfileChange));
        _ragSystemPrompt = _appSettings.RagSystemPrompt;
        _ragMaxHistoryTokens = _appSettings.RagMaxHistoryTokens;
        _ragMode = _appSettings.RagMode;
        _rerankEnabled = _appSettings.RerankEnabled;
        _rerankModel = _appSettings.RerankModel;
        _rerankRecall = _appSettings.RerankRecall;
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
        _savedGithubTokenAtLoad = _githubToken;
        _savedBaseUrlAtLoad = _llmBaseUrl;
        _savedModelAtLoad = _llmModel;
        _savedRagSystemPromptAtLoad = _ragSystemPrompt;
        // 「输入即生效」基准快照：后端运行时当前即这些值，启动后不重复推送
        SyncLlmAppliedSnapshot();
        // 批次 2：保存重启类字段快照（用于变更检测）
        _savedBackendUrlAtLoad = _backendUrl;
        _savedStartupTimeoutSecAtLoad = _startupTimeoutSec;
        _savedBackendCommandAtLoad = _backendCommand;

        // 载入 AI 提供商档案（ApiKey 已在 App.LoadSettings 解密为明文；解密失败项带 KeyDecryptFailed 标记）
        SavedProfiles.Clear();
        if (_appSettings.LlmProfiles is { Count: > 0 })
        {
            foreach (var profile in _appSettings.LlmProfiles.Where(p => p is not null))
            {
                SavedProfiles.Add(profile);
            }
        }
        // 默认选中最后应用的档案（仅高亮，不自动应用/改配置）
        if (!string.IsNullOrWhiteSpace(_appSettings.ActiveProfileId))
        {
            SelectedProfile = SavedProfiles.FirstOrDefault(p => p.Id == _appSettings.ActiveProfileId);
        }
        else
        {
            SelectedProfile = SavedProfiles.FirstOrDefault();
        }
        OnPropertyChanged(nameof(HasSavedProfiles));

        // 智能匹配预设服务商
        _selectedPreset = AvailablePresets.FirstOrDefault(p =>
            p.Id != "custom" &&
            (p.Provider == _llmProvider && !string.IsNullOrWhiteSpace(p.BaseUrl) && _llmBaseUrl != null && _llmBaseUrl.StartsWith(p.BaseUrl, StringComparison.OrdinalIgnoreCase))
        ) ?? (AvailablePresets.FirstOrDefault(p => p.Provider == _llmProvider) ?? AvailablePresets[0]);

        // 重建合并服务商下拉（内置预设 + 自定义服务商）；重建期间只高亮不应用
        RebuildProviderOptions();

        // 已配置的模型补进下拉候选并选中：否则打开设置页时下拉首屏选中态空白
        SyncSelectedModelCandidate();

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

        _isInitializing = false;
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
                OnPropertyChanged(nameof(BundleRecommendations));
                OnPropertyChanged(nameof(HasBundleRecommendations));
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

    /// <summary>档位整套搭配（bundle）条目；当前档位排最前。干净电脑也有完整 5 档。</summary>
    public List<AutoSetupRecommendation> BundleRecommendations
        => LocalAiEnv?.Recommendations?
               .Where(r => r.Kind == "bundle")
               .OrderByDescending(r => r.IsCurrentTier)
               .ToList()
           ?? new List<AutoSetupRecommendation>();

    public bool HasBundleRecommendations => BundleRecommendations.Count > 0;

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

    // ===== 机型档位整套搭配（bundle） =====

    /// <summary>打开 Ollama 下载页钩子（测试用；null = 真实 Process.Start）。</summary>
    internal Action<string>? OpenInstallerUrlOverride;

    /// <summary>「是否打开下载页」确认钩子（测试用；null = 真实 MessageBox）。</summary>
    internal Func<string?, bool>? ConfirmInstallerOpenOverride;

    // ===================== 自定义搭配：对话 / 嵌入 / 重排 可分开选 =====================

    /// <summary>可选嵌入模型（知识库向量；换模型需重建索引）。</summary>
    public System.Collections.ObjectModel.ObservableCollection<CustomEmbedOption> EmbedOptions { get; } = new()
    {
        new("BAAI/bge-small-zh-v1.5", "bge-small-zh（轻量 · 内置默认）", "约 100MB，CPU 可跑，中文够用"),
        new("jinaai/jina-embeddings-v2-base-zh", "jina-zh（中文检索更强）", "约 500MB，需重建索引"),
        new("intfloat/multilingual-e5-large", "e5-large（多语言最强）", "约 1.2GB，较吃资源，需重建索引"),
    };

    /// <summary>可选本地对话模型（Ollama / LM Studio；与档位解耦，可混搭）。</summary>
    public System.Collections.ObjectModel.ObservableCollection<CustomChatOption> ChatModelOptions { get; } = new()
    {
        new("qwen3:0.6b", "Qwen3-0.6B", "极轻 · 约 0.6GB · 质量偏弱"),
        new("qwen3:1.7b", "Qwen3-1.7B", "轻 · 约 1.4GB · CPU 可用"),
        new("qwen3:4b", "Qwen3-4B", "较轻 · 约 2.6GB · 入门显卡"),
        new("phi4-mini:3.8b", "Phi-4-mini 3.8B", "较轻 · 约 2.8GB · 编码/推理更强"),
        new("qwen3:8b", "Qwen3-8B", "中等 · 约 5.2GB · 主流显卡全速"),
        new("qwen3:14b", "Qwen3-14B", "较重 · 约 9GB · 12GB+ 显存"),
        new("qwen3:30b-a3b", "Qwen3-30B-A3B (MoE)", "重 · 约 19GB · 24GB+ 显存"),
        new("qwen3:32b", "Qwen3-32B", "最重 · 约 20GB · 质量最强更慢"),
    };

    private CustomEmbedOption? _customEmbed;
    private CustomChatOption? _customChat;
    private bool _customRerankEnabled = true;

    public CustomEmbedOption? CustomEmbed
    {
        get => _customEmbed;
        set
        {
            if (SetProperty(ref _customEmbed, value))
            {
                if (!_suppressPresetAutoCustom && SelectedPresetId != "custom") { SelectedPresetId = "custom"; NotifyPresetFlags(); }
                UpdateConfigLoadSummary();
            }
        }
    }

    public CustomChatOption? CustomChat
    {
        get => _customChat;
        set
        {
            if (SetProperty(ref _customChat, value))
            {
                if (!_suppressPresetAutoCustom && SelectedPresetId != "custom") { SelectedPresetId = "custom"; NotifyPresetFlags(); }
                UpdateConfigLoadSummary();
            }
        }
    }

    /// <summary>检索后重排（提升精度、增加延迟/占用；纯 CPU 建议关）。</summary>
    public bool CustomRerankEnabled
    {
        get => _customRerankEnabled;
        set
        {
            if (SetProperty(ref _customRerankEnabled, value))
            {
                if (!_suppressPresetAutoCustom && SelectedPresetId != "custom") { SelectedPresetId = "custom"; NotifyPresetFlags(); }
                UpdateConfigLoadSummary();
            }
        }
    }

    // ===================== 一体化模型配置（预设填表 + 统一应用） =====================

    private bool _useLocalChat = true;

    /// <summary>使用本地对话（Ollama / LM Studio）。</summary>
    public bool UseLocalChat
    {
        get => _useLocalChat;
        set
        {
            if (SetProperty(ref _useLocalChat, value))
            {
                if (value) UseCloudChat = false;
                OnPropertyChanged(nameof(UseCloudChat));
            }
        }
    }

    private string _cloudProtocol = "openai";

    /// <summary>云端协议：openai | openai-compatible | anthropic | gemini。</summary>
    public string CloudProtocol
    {
        get => _cloudProtocol;
        set
        {
            if (SetProperty(ref _cloudProtocol, value ?? "openai"))
            {
                ApplyCloudProtocolDefaults();
                OnPropertyChanged(nameof(CloudProtocolLabel));
            }
        }
    }

    public string CloudProtocolLabel => _cloudProtocol switch
    {
        "anthropic" => "Anthropic（Claude）",
        "gemini" => "Google（Gemini）",
        "openai-compatible" => "OpenAI 兼容（自定义）",
        _ => "OpenAI",
    };

    private void ApplyCloudProtocolDefaults()
    {
        switch (_cloudProtocol)
        {
            case "anthropic":
                if (string.IsNullOrWhiteSpace(LlmBaseUrl) || LlmBaseUrl.Contains("openai") || LlmBaseUrl.Contains("11434") || LlmBaseUrl.Contains("1234"))
                    LlmBaseUrl = "https://api.anthropic.com";
                if (string.IsNullOrWhiteSpace(LlmModel) || LlmModel.StartsWith("gpt") || LlmModel.StartsWith("qwen"))
                    LlmModel = "claude-sonnet-4-5";
                LlmProvider = "anthropic";
                break;
            case "gemini":
                if (string.IsNullOrWhiteSpace(LlmBaseUrl) || LlmBaseUrl.Contains("openai") || LlmBaseUrl.Contains("11434") || LlmBaseUrl.Contains("1234"))
                    LlmBaseUrl = "https://generativelanguage.googleapis.com/v1beta/openai";
                if (string.IsNullOrWhiteSpace(LlmModel) || LlmModel.StartsWith("gpt") || LlmModel.StartsWith("claude") || LlmModel.StartsWith("qwen"))
                    LlmModel = "gemini-2.5-flash";
                LlmProvider = "gemini";
                break;
            case "openai-compatible":
                LlmProvider = "openai";
                break;
            default:
                if (string.IsNullOrWhiteSpace(LlmBaseUrl) || LlmBaseUrl.Contains("anthropic") || LlmBaseUrl.Contains("11434"))
                    LlmBaseUrl = "https://api.openai.com/v1";
                if (string.IsNullOrWhiteSpace(LlmModel) || LlmModel.StartsWith("claude") || LlmModel.StartsWith("gemini"))
                    LlmModel = "gpt-4o-mini";
                LlmProvider = "openai";
                break;
        }
    }

    /// <summary>使用云端 API 对话。</summary>
    public bool UseCloudChat
    {
        get => !_useLocalChat;
        set
        {
            if (SetProperty(ref _useLocalChat, !value))
            {
                OnPropertyChanged(nameof(UseLocalChat));
                OnPropertyChanged(nameof(UseCloudChat));
            }
        }
    }

    private bool _showAdvancedBundles;

    /// <summary>是否展开「高级：整套档位安装」。</summary>
    public bool ShowAdvancedBundles
    {
        get => _showAdvancedBundles;
        set => SetProperty(ref _showAdvancedBundles, value);
    }

    private string _selectedPresetId = "balanced";
    private bool _suppressPresetAutoCustom;
    private string _runtimeChoice = "ollama";
    private string _configLoadSummary = "";

    /// <summary>当前预设 id：ultralight / light / balanced / performance / custom。</summary>
    public string SelectedPresetId
    {
        get => _selectedPresetId;
        private set => SetProperty(ref _selectedPresetId, value);
    }

    /// <summary>本地运行时：ollama / lmstudio。</summary>
    public string RuntimeChoice
    {
        get => _runtimeChoice;
        set
        {
            if (SetProperty(ref _runtimeChoice, value))
                UpdateConfigLoadSummary();
        }
    }

    /// <summary>负载摘要（选完即变，便于「轻压力」决策）。</summary>
    public string ConfigLoadSummary
    {
        get => _configLoadSummary;
        private set => SetProperty(ref _configLoadSummary, value);
    }

    public bool IsPresetUltralight => SelectedPresetId == "ultralight";
    public bool IsPresetLight => SelectedPresetId == "light";
    public bool IsPresetBalanced => SelectedPresetId == "balanced";
    public bool IsPresetPerformance => SelectedPresetId == "performance";
    public bool IsPresetCustom => SelectedPresetId == "custom";

    private void NotifyPresetFlags()
    {
        OnPropertyChanged(nameof(IsPresetUltralight));
        OnPropertyChanged(nameof(IsPresetLight));
        OnPropertyChanged(nameof(IsPresetBalanced));
        OnPropertyChanged(nameof(IsPresetPerformance));
        OnPropertyChanged(nameof(IsPresetCustom));
        OnPropertyChanged(nameof(SelectedPresetId));
    }

    /// <summary>预设只「填表」，不直接写配置；点「应用并保存」才生效。</summary>
    private void ApplyPresetInternal(string presetId, bool markDirty = true)
    {
        _suppressPresetAutoCustom = true;
        try
        {
            ApplyPresetCore(presetId);
        }
        finally
        {
            _suppressPresetAutoCustom = false;
        }
        SelectedPresetId = presetId;
        NotifyPresetFlags();
        UpdateConfigLoadSummary();
        if (markDirty)
            IsDirty = true;
    }

    private void ApplyPresetCore(string presetId)
    {
        switch (presetId)
        {
            case "ultralight":
                CustomChat = ChatModelOptions.FirstOrDefault(o => o.Id == "qwen3:1.7b");
                CustomEmbed = EmbedOptions.FirstOrDefault(o => o.Id == "BAAI/bge-small-zh-v1.5");
                CustomRerankEnabled = false;
                break;
            case "light":
                CustomChat = ChatModelOptions.FirstOrDefault(o => o.Id == "qwen3:4b");
                CustomEmbed = EmbedOptions.FirstOrDefault(o => o.Id == "BAAI/bge-small-zh-v1.5");
                CustomRerankEnabled = true;
                break;
            case "balanced":
                CustomChat = ChatModelOptions.FirstOrDefault(o => o.Id == "qwen3:8b")
                    ?? ChatModelOptions.FirstOrDefault(o => o.Id == "qwen3:4b");
                CustomEmbed = EmbedOptions.FirstOrDefault(o => o.Id == "BAAI/bge-small-zh-v1.5");
                CustomRerankEnabled = true;
                break;
            case "performance":
                CustomChat = ChatModelOptions.FirstOrDefault(o => o.Id == "qwen3:14b")
                    ?? ChatModelOptions.LastOrDefault();
                CustomEmbed = EmbedOptions.FirstOrDefault(o => o.Id == "jinaai/jina-embeddings-v2-base-zh")
                    ?? EmbedOptions.LastOrDefault();
                CustomRerankEnabled = true;
                break;
            default:
                return;
        }
    }

    [RelayCommand]
    public void ApplyPreset(string? presetId) => ApplyPresetInternal(presetId ?? "balanced");

    private void UpdateConfigLoadSummary()
    {
        var chat = CustomChat?.Id ?? "";
        var load = chat switch
        {
            "qwen3:0.6b" or "qwen3:1.7b" => "轻",
            "qwen3:4b" or "phi4-mini:3.8b" => "较轻",
            "qwen3:8b" => "中等",
            "qwen3:14b" => "较重",
            _ => "重",
        };
        var embed = CustomEmbed?.Id ?? "";
        var embedLabel = embed switch
        {
            "BAAI/bge-small-zh-v1.5" => "嵌入轻量",
            "jinaai/jina-embeddings-v2-base-zh" => "嵌入中等",
            "intfloat/multilingual-e5-large" => "嵌入较重",
            _ => "嵌入自定义",
        };
        var rt = RuntimeChoice == "lmstudio" ? "LM Studio" : "Ollama";
        ConfigLoadSummary = $"{rt} · 对话负载 {load} · {embedLabel} · 重排 {(CustomRerankEnabled ? "开" : "关")}";
    }

    /// <summary>统一应用：运行时 + 对话 + 嵌入 + 重排。</summary>
    [RelayCommand]
    public void ApplyUnifiedConfig()
    {
        if (CustomChat is null && CustomEmbed is null)
        {
            _notifications.Warning("请至少选择对话模型或嵌入模型", "模型配置");
            return;
        }

        if (UseLocalChat)
        {
            if (RuntimeChoice == "lmstudio")
            {
                LlmProvider = "openai";
                LlmBaseUrl = string.IsNullOrWhiteSpace(LocalAiEnv?.LmStudio?.BaseUrl)
                    ? "http://127.0.0.1:1234/v1"
                    : LocalAiEnv.LmStudio.BaseUrl;
            }
            else
            {
                LlmProvider = "ollama";
                LlmBaseUrl = string.IsNullOrWhiteSpace(LocalAiEnv?.Ollama?.BaseUrl)
                    ? "http://127.0.0.1:11434"
                    : LocalAiEnv.Ollama.BaseUrl;
            }
            if (CustomChat is not null)
            {
                LlmModel = CustomChat.Id;
                LlmApiKey = "";
            }
        }
        else
        {
            // 云端 API：按协议设置 provider，地址/模型/Key 用表单值
            LlmProvider = CloudProtocol switch
            {
                "anthropic" => "anthropic",
                "gemini" => "gemini",
                _ => "openai",
            };
        }
        if (CustomEmbed is not null)
        {
            EmbedModel = CustomEmbed.Id;
        }
        RerankEnabled = CustomRerankEnabled;
        if (!CustomRerankEnabled)
            RerankModel = "BAAI/bge-reranker-base";

        StatusMessage = $"已应用：{ConfigLoadSummary}";
        IsDirty = true;
        _ = ApplyUnifiedConfigAsync();
    }

    private async Task ApplyUnifiedConfigAsync()
    {
        StartProgress("应用模型配置", "写入设置并同步后端…");
        try
        {
            // 本地模式：服务未运行则静默拉起（带进度）
            if (UseLocalChat)
            {
                var running = RuntimeChoice == "lmstudio"
                    ? LocalAiEnv?.LmStudio?.Running == true
                    : LocalAiEnv?.Ollama?.Running == true;
                if (!running)
                {
                    UpdateProgress(detail: "正在启动本地运行时…");
                    await Task.Run(() => StartInstalledRuntime(null));
                    await Task.Delay(1500);
                }
            }

            UpdateProgress(50, "测试连接…");
            await TestConnectionAsync();

            UpdateProgress(80, "刷新模型列表…");
            await RefreshLlmModelsAsync();

            UpdateProgress(100, "完成");
            StopProgress("配置已生效");
            _notifications.Success("模型配置已应用；若更换了嵌入模型，请重建索引", "模型配置");
        }
        catch (Exception ex)
        {
            StopProgress("失败");
            _notifications.Error($"应用配置失败：{ex.Message}", "模型配置");
        }
    }

    /// <summary>应用自定义搭配：嵌入 / 对话 / 重排 独立生效。</summary>
    [RelayCommand]
    public void ApplyCustomMix()
    {
        if (CustomEmbed is null && CustomChat is null)
        {
            _notifications.Warning("请先选择对话模型或嵌入模型", "自定义搭配");
            return;
        }

        if (CustomEmbed is not null)
        {
            EmbedModel = CustomEmbed.Id;
        }
        RerankEnabled = CustomRerankEnabled;
        if (!CustomRerankEnabled)
            RerankModel = "BAAI/bge-reranker-base";

        if (LocalAiEnv?.Ollama?.Running == true)
        {
            LlmProvider = "ollama";
            LlmBaseUrl = string.IsNullOrWhiteSpace(LocalAiEnv.Ollama.BaseUrl) ? "http://127.0.0.1:11434" : LocalAiEnv.Ollama.BaseUrl;
        }
        else if (LocalAiEnv?.LmStudio?.Running == true)
        {
            LlmProvider = "openai";
            LlmBaseUrl = string.IsNullOrWhiteSpace(LocalAiEnv.LmStudio.BaseUrl) ? "http://127.0.0.1:1234/v1" : LocalAiEnv.LmStudio.BaseUrl;
        }
        else
        {
            LlmProvider = "ollama";
            LlmBaseUrl = "http://127.0.0.1:11434";
        }

        if (CustomChat is not null)
        {
            LlmModel = CustomChat.Id;
            LlmApiKey = "";
        }

        var embedPart = CustomEmbed is not null ? $"嵌入 {CustomEmbed.Id}" : "嵌入未改";
        var chatPart = CustomChat is not null ? $"对话 {CustomChat.Id}" : "对话未改";
        StatusMessage = $"已应用自定义搭配：{embedPart} · {chatPart} · 重排 {(CustomRerankEnabled ? "开" : "关")}";
        _notifications.Success("自定义搭配已应用；若更换了嵌入模型，请重建索引", "自定义搭配");
        _ = TestConnectionAsync();
        _ = RefreshLlmModelsAsync();
        IsDirty = true;
    }

    [RelayCommand]
    public void ApplyBundle(AutoSetupRecommendation? bundle)
    {
        if (bundle is null || bundle.Kind != "bundle") return;

        // 后端四态分流（C# 侧对 installed 态做二次校验，防止探测滞后误开下载页）：
        // ready/model_missing → 应用整套配置
        // installed_stopped   → 自动启动已装运行时
        // runtime_missing     → 确认后才打开 Ollama 下载页
        if (bundle.State == "installed_stopped")
        {
            StartInstalledRuntime(bundle);
            return;
        }
        if (bundle.State == "runtime_missing")
        {
            // 二次校验：本机明明探测到已安装（后端 state 可能滞后于刚装好的场景）→ 不下载，改走启动
            if (LocalAiEnv?.Ollama?.Installed == true || LocalAiEnv?.LmStudio?.Installed == true)
            {
                _notifications.Warning("本机已检测到 Ollama / LM Studio 安装，无需下载。请手动启动服务并开启 Local Server 后点「重新探测」，或点「自定义地址」直接绑定。", "无需下载");
                return;
            }
            // 打开下载页前显式确认（避免误触导致浏览器反复弹下载页）
            if (ConfirmInstallerOpenOverride is not null)
            {
                if (!ConfirmInstallerOpenOverride(bundle.InstallerUrl)) return;
            }
            else
            {
                var confirm = System.Windows.MessageBox.Show(
                    "本机未检测到 Ollama / LM Studio 服务。\n\n" +
                    "是否打开 Ollama 下载页？（装好后回来点「重新探测」即可）",
                    "安装 Ollama", System.Windows.MessageBoxButton.YesNo, System.Windows.MessageBoxImage.Question);
                if (confirm != System.Windows.MessageBoxResult.Yes) return;
            }
            if (!string.IsNullOrWhiteSpace(bundle.InstallerUrl))
            {
                if (OpenInstallerUrlOverride is not null)
                {
                    OpenInstallerUrlOverride(bundle.InstallerUrl);
                }
                else
                {
                    try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(bundle.InstallerUrl) { UseShellExecute = true }); }
                    catch (Exception ex) { DebugLog.Warn($"打开 Ollama 下载页失败: {ex.Message}", "Settings"); }
                }
            }
            return;
        }

        // 整套下发：嵌入 + 重排（极轻量档关闭）+ LLM 三字段一次设置
        EmbedModel = bundle.EmbedModel;
        RerankEnabled = bundle.RerankEnabled;
        if (!bundle.RerankEnabled || string.IsNullOrWhiteSpace(bundle.RerankModel))
        {
            RerankModel = "BAAI/bge-reranker-base"; // 保持合法值，开关已关不会参与检索
        }
        else
        {
            RerankModel = bundle.RerankModel;
        }
        // provider/base_url 按实际运行的运行时选择：
        // Ollama 走原生协议（URL 不带 /v1）；LM Studio 走 OpenAI 兼容（带 /v1）；
        // 都没探测到时兜底 Ollama 默认地址。固定写 openai+11434/v1 在 Ollama 运行时连接必败。
        if (LocalAiEnv?.Ollama?.Running == true)
        {
            LlmProvider = "ollama";
            LlmBaseUrl = string.IsNullOrWhiteSpace(LocalAiEnv.Ollama.BaseUrl) ? "http://127.0.0.1:11434" : LocalAiEnv.Ollama.BaseUrl;
        }
        else if (LocalAiEnv?.LmStudio?.Running == true)
        {
            LlmProvider = "openai";
            LlmBaseUrl = string.IsNullOrWhiteSpace(LocalAiEnv.LmStudio.BaseUrl) ? "http://127.0.0.1:1234/v1" : LocalAiEnv.LmStudio.BaseUrl;
        }
        else
        {
            LlmProvider = "openai";
            LlmBaseUrl = "http://127.0.0.1:11434/v1";
        }
        LlmApiKey = "";
        if (!string.IsNullOrWhiteSpace(bundle.Model))
        {
            LlmModel = bundle.Model;
        }
        StatusMessage = $"✅ 已应用「{bundle.Title}」整套搭配（模型: {LlmModel}）";
        _notifications.Success($"已应用整套搭配（{bundle.Title}），嵌入模型变更后需重建索引", "机型档位");
        _ = TestConnectionAsync();
        _ = RefreshLlmModelsAsync();
    }

    [RelayCommand]
    public void CopyPullCommand(AutoSetupRecommendation? bundle)
    {
        if (bundle is null || string.IsNullOrWhiteSpace(bundle.PullCommand)) return;
        try
        {
            System.Windows.Clipboard.SetText(bundle.PullCommand);
            _notifications.Info($"已复制：{bundle.PullCommand}", "拉取命令");
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"复制拉取命令失败: {ex.Message}", "Settings");
        }
    }

    // ===================== 一键安装（开箱即用）：确认一次 → 后台拉取 → 进度 → 自动应用整套 =====================

    private string _pullState = "idle"; // idle / pulling / done / error
    public string PullState { get => _pullState; private set { if (SetProperty(ref _pullState, value)) { OnPropertyChanged(nameof(IsPulling)); OnPropertyChanged(nameof(PullProgressText)); } } }

    private double _pullPercent;
    public double PullPercent { get => _pullPercent; private set { if (SetProperty(ref _pullPercent, value)) OnPropertyChanged(nameof(PullProgressText)); } }

    private string _pullModel = "";
    public string PullModel { get => _pullModel; private set => SetProperty(ref _pullModel, value); }

    public bool IsPulling => PullState == "pulling";
    public string PullProgressText => PullState switch
    {
        "pulling" => $"正在下载 {PullModel}… {PullPercent:0}%",
        "done" => $"{PullModel} 下载完成",
        "error" => "下载失败（可在设置页重试或复制命令手动拉取）",
        _ => "",
    };

    /// <summary>「一键安装」确认钩子（测试注入；null = 真实 MessageBox）。</summary>
    internal Func<AutoSetupRecommendation, bool>? ConfirmOneClickSetupOverride;

    /// <summary>一键安装：model_missing 态直接由应用代跑 ollama pull（用户点击即授权），完成后自动应用整套配置。</summary>
    [RelayCommand]
    public async Task OneClickSetupAsync(AutoSetupRecommendation? bundle)
    {
        if (bundle is null || bundle.Kind != "bundle" || IsPulling) return;
        if (bundle.State == "runtime_missing" || bundle.State == "installed_stopped")
        {
            ApplyBundle(bundle); // 缺运行时/未启动走既有四态分流
            return;
        }

        // 一键安装走魔搭加速源：从 pull_command 提取完整模型引用
        // （如 "modelscope.cn/Qwen/Qwen3-8B-GGUF:Q4_K_M"），bundle.Model 的短名
        // （qwen3:8b）走 ollama.com 官方源，国内拉取慢。
        // 提取失败时才回退短名（此时退化为官方源，仍可用）。
        var pullRef = bundle.PullCommand;
        if (pullRef.StartsWith("ollama pull ", StringComparison.OrdinalIgnoreCase))
            pullRef = pullRef["ollama pull ".Length..].Trim();
        if (string.IsNullOrWhiteSpace(pullRef) || !pullRef.Contains('/'))
            pullRef = bundle.Model; // 不像完整引用 → 回退
        var model = pullRef;
        if (string.IsNullOrWhiteSpace(model))
        {
            _notifications.Warning("该档位没有可拉取的对话模型", "一键安装");
            return;
        }

        // 一次性确认（告知磁盘占用，用户点击即授权代拉）
        var sizeGb = bundle.ChatOptions.FirstOrDefault(o => o.ModelId == bundle.Model)?.SizeGb ?? 0;
        var sizeText = sizeGb > 0 ? $"约 {sizeGb:0.#}GB" : "数 GB";
        if (ConfirmOneClickSetupOverride is not null)
        {
            if (!ConfirmOneClickSetupOverride(bundle)) return;
        }
        else
        {
            var confirm = System.Windows.MessageBox.Show(
                $"将自动下载本地模型「{model}」（{sizeText}），下载完成后自动配置嵌入 / 对话 / 重排三件套。\n\n是否继续？",
                "一键配置本地 AI", System.Windows.MessageBoxButton.YesNo, System.Windows.MessageBoxImage.Question);
            if (confirm != System.Windows.MessageBoxResult.Yes) return;
        }

        try
        {
            var st = await _apiService.StartOllamaPullAsync(model);
            PullState = st.State; PullPercent = st.Percent; PullModel = model;
            if (st.State == "error")
            {
                _notifications.Error($"拉取失败：{st.Error}", "一键安装");
                return;
            }
            // 轮询进度直至终态（2s 间隔，最长 30 分钟）
            for (var i = 0; i < 900; i++)
            {
                await Task.Delay(2000);
                var s = await _apiService.GetOllamaPullStatusAsync();
                PullState = s.State; PullPercent = s.Percent;
                if (s.State is "done" or "error") break;
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"一键拉取异常: {ex.Message}", "Settings");
            PullState = "error";
            _notifications.Error($"拉取失败：{ex.Message}", "一键安装");
            return;
        }

        if (PullState != "done")
        {
            _notifications.Error("模型下载未完成，可稍后在设置页重试", "一键安装");
            return;
        }

        // 拉取完成 → 重新探测（模型已就绪会变 ready）→ 应用整套
        await DetectLocalAiAsync();
        var ready = BundleRecommendations.FirstOrDefault(b => b.Tier == bundle.Tier) ?? bundle;
        ApplyBundle(ready);
        _notifications.Success($"「{bundle.Title}」已就绪并完成配置", "一键安装");
    }

    // ===================== 统一可视化进度（>5s 操作必用） =====================

    private bool _isProgressVisible;
    private string _progressTitle = "";
    private string _progressDetail = "";
    private double _progressPercent = -1; // <0 = 不定进度
    private int _progressElapsedSec;
    private System.Threading.CancellationTokenSource? _progressCts;

    public bool IsProgressVisible
    {
        get => _isProgressVisible;
        private set
        {
            if (SetProperty(ref _isProgressVisible, value))
                OnPropertyChanged(nameof(HasProgressCancel));
        }
    }

    public string ProgressTitle
    {
        get => _progressTitle;
        private set => SetProperty(ref _progressTitle, value);
    }

    public string ProgressDetail
    {
        get => _progressDetail;
        private set => SetProperty(ref _progressDetail, value);
    }

    /// <summary>0-100；-1 表示不定进度（转圈）。</summary>
    public double ProgressPercent
    {
        get => _progressPercent;
        private set
        {
            if (SetProperty(ref _progressPercent, value))
                OnPropertyChanged(nameof(IsProgressIndeterminate));
        }
    }

    public bool IsProgressIndeterminate => _progressPercent < 0;
    public bool HasProgressCancel => IsProgressVisible;

    public string ProgressElapsedText => _progressElapsedSec < 60
        ? $"{_progressElapsedSec}s"
        : $"{_progressElapsedSec / 60}m{_progressElapsedSec % 60:00}s";

    private void StartProgress(string title, string detail = "")
    {
        ProgressTitle = title;
        ProgressDetail = detail;
        ProgressPercent = -1;
        _progressElapsedSec = 0;
        OnPropertyChanged(nameof(ProgressElapsedText));
        IsProgressVisible = true;
        _progressCts?.Cancel();
        _progressCts = new System.Threading.CancellationTokenSource();
        var ct = _progressCts.Token;
        _ = Task.Run(async () =>
        {
            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(1000, ct).ContinueWith(_ => { });
                if (ct.IsCancellationRequested) break;
                _progressElapsedSec++;
                await System.Windows.Application.Current.Dispatcher.InvokeAsync(() =>
                {
                    OnPropertyChanged(nameof(ProgressElapsedText));
                });
            }
        }, ct);
    }

    private void UpdateProgress(double percent = -1, string? detail = null)
    {
        if (percent >= 0) ProgressPercent = percent;
        if (detail is not null) ProgressDetail = detail;
    }

    private void StopProgress(string? doneDetail = null)
    {
        _progressCts?.Cancel();
        _progressCts = null;
        if (doneDetail is not null) ProgressDetail = doneDetail;
        IsProgressVisible = false;
    }

    [RelayCommand]
    private void CancelProgress()
    {
        _progressCts?.Cancel();
        StopProgress("已取消");
        StatusMessage = "操作已取消";
    }

    [RelayCommand]
    public void StartInstalledRuntime(AutoSetupRecommendation? bundle)
    {
        // 资源策略：仅当前对话走本地模型时才拉起 Ollama / LM Studio
        if (!UseLocalChat)
        {
            _notifications.Info("当前对话使用云端 API，无需启动本地运行时", "按需启动");
            return;
        }

        var targetName = "本地运行时";
        string? installPath = null;
        string? cli = null;
        if (LocalAiEnv?.Ollama?.Installed == true)
        {
            targetName = "Ollama";
            installPath = LocalAiEnv.Ollama.InstallPath;
            cli = "ollama";
        }
        else if (LocalAiEnv?.LmStudio?.Installed == true)
        {
            targetName = "LM Studio";
            installPath = LocalAiEnv.LmStudio.InstallPath;
        }
        else
        {
            _notifications.Warning("未检测到已安装的 Ollama / LM Studio", "运行时启动");
            return;
        }

        StartProgress($"正在静默启动 {targetName}", "进程已拉起，等待服务就绪…");
        StatusMessage = $"正在启动 {targetName}…";
        _ = RunStartRuntimeWithProgressAsync(targetName, installPath, cli);
    }

    private async Task RunStartRuntimeWithProgressAsync(string name, string? installPath, string? cli)
    {
        try
        {
            var ok = TryStartRuntime(installPath, cli);
            if (!ok)
            {
                StopProgress("启动失败");
                _notifications.Warning($"{name} 启动失败，请手动打开后点「重新探测」", "运行时启动");
                return;
            }

            for (var i = 0; i < 15; i++)
            {
                UpdateProgress(detail: $"等待 {name} 服务就绪… {(i + 1) * 2}s");
                await Task.Delay(2000);
                await DetectLocalAiAsync();
                var running = name == "Ollama"
                    ? LocalAiEnv?.Ollama?.Running == true
                    : LocalAiEnv?.LmStudio?.Running == true;
                if (running)
                {
                    StopProgress($"{name} 已就绪");
                    _notifications.Success($"{name} 已启动并就绪", "运行时启动");
                    StatusMessage = $"{name} 已就绪";
                    return;
                }
            }
            StopProgress("等待超时");
            _notifications.Warning($"{name} 可能仍在启动，请稍后点「重新探测」", "运行时启动");
        }
        catch (Exception ex)
        {
            StopProgress("异常");
            DebugLog.Warn($"启动 {name} 进度流程失败: {ex.Message}", "Settings");
        }
    }

    private static bool TryStartRuntime(string? installPath, string? cliName)
    {
        try
        {
            if (!string.IsNullOrWhiteSpace(installPath) && System.IO.File.Exists(installPath))
            {
                // LM Studio 主程序启动即服务；Ollama 主程序同理（带 GUI 时服务随启动）
                var guiPsi = new System.Diagnostics.ProcessStartInfo(installPath)
                {
                    UseShellExecute = true,
                    WindowStyle = System.Diagnostics.ProcessWindowStyle.Minimized,
                };
                System.Diagnostics.Process.Start(guiPsi);
                return true;
            }
            if (cliName is not null)
            {
                // 静默启动：不弹 cmd 黑框，不抢前台
                var psi = new System.Diagnostics.ProcessStartInfo(cliName, "serve")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                System.Diagnostics.Process.Start(psi);
                return true;
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"启动运行时失败 ({installPath ?? cliName}): {ex.Message}", "Settings");
        }
        return false;
    }

    [RelayCommand]
    public void ConfigureCustomRuntimeUrl(AutoSetupRecommendation? bundle)
    {
        // 用户自行配置服务地址（覆盖所有四态：装了没被扫到 / 服务在非常规端口 / 远程地址）
        var isOllama = LocalAiEnv?.Ollama?.Installed == true || LocalAiEnv?.Ollama?.Running == true;
        var defaultUrl = isOllama ? "http://127.0.0.1:11434" : "http://127.0.0.1:1234/v1";
        var title = isOllama ? "Ollama" : "LM Studio / OpenAI 兼容服务";
        var input = ShowTextInput(
            "自定义本地服务地址",
            $"输入 {title} 的 Local Server 地址（OpenAI 兼容需带 /v1）：",
            defaultUrl);
        if (string.IsNullOrWhiteSpace(input)) return;

        var url = input.Trim().TrimEnd('/');
        var provider = LocalAiEnv?.Ollama?.Running == true || LocalAiEnv?.Ollama?.Installed == true ? "ollama" : "openai";
        if (provider == "openai" && !url.EndsWith("/v1"))
        {
            var addV1 = System.Windows.MessageBox.Show(
                "OpenAI 兼容协议（LM Studio）的 base_url 通常需要 /v1 后缀。\n\n是否自动补上 /v1？",
                "地址确认", System.Windows.MessageBoxButton.YesNo, System.Windows.MessageBoxImage.Question);
            if (addV1 == System.Windows.MessageBoxResult.Yes)
                url += "/v1";
        }

        LlmProvider = provider;
        LlmBaseUrl = url;
        LlmApiKey = "";
        _notifications.Success($"已设置 {title} 地址：{url}，请保存后测试连接", "自定义地址");
        _ = TestConnectionAsync();
        _ = RefreshLlmModelsAsync();
    }

    /// <summary>代码级单行输入对话框（项目无现成 XAML 输入窗体，避免新增资源引用风险）。
    /// 返回 null 表示取消。</summary>
    private static string? ShowTextInput(string title, string prompt, string initial)
    {
        var dlg = new System.Windows.Window
        {
            Title = title,
            Width = 460,
            Height = 190,
            WindowStartupLocation = System.Windows.WindowStartupLocation.CenterOwner,
            Owner = System.Windows.Application.Current.MainWindow,
            ResizeMode = System.Windows.ResizeMode.NoResize,
            WindowStyle = System.Windows.WindowStyle.ToolWindow,
        };
        var panel = new System.Windows.Controls.StackPanel { Margin = new System.Windows.Thickness(16) };
        var label = new System.Windows.Controls.TextBlock
        {
            Text = prompt,
            TextWrapping = System.Windows.TextWrapping.Wrap,
            Margin = new System.Windows.Thickness(0, 0, 0, 8),
        };
        var box = new System.Windows.Controls.TextBox
        {
            Text = initial,
            Padding = new System.Windows.Thickness(6, 4, 6, 4),
            FontSize = 13,
        };
        panel.Children.Add(label);
        panel.Children.Add(box);

        var buttons = new System.Windows.Controls.StackPanel
        {
            Orientation = System.Windows.Controls.Orientation.Horizontal,
            HorizontalAlignment = System.Windows.HorizontalAlignment.Right,
            Margin = new System.Windows.Thickness(0, 12, 0, 0),
        };
        string? result = null;
        var okBtn = new System.Windows.Controls.Button { Content = "确定", MinWidth = 72, Padding = new System.Windows.Thickness(10, 5, 10, 5), Margin = new System.Windows.Thickness(0, 0, 8, 0) };
        okBtn.Click += (_, _) => { result = box.Text; dlg.DialogResult = true; };
        var cancelBtn = new System.Windows.Controls.Button { Content = "取消", MinWidth = 72, Padding = new System.Windows.Thickness(10, 5, 10, 5) };
        cancelBtn.Click += (_, _) => { dlg.DialogResult = false; };
        buttons.Children.Add(okBtn);
        buttons.Children.Add(cancelBtn);
        panel.Children.Add(buttons);
        dlg.Content = panel;

        // 回车确定
        box.KeyDown += (_, e) =>
        {
            if (e.Key == System.Windows.Input.Key.Enter)
            {
                result = box.Text;
                dlg.DialogResult = true;
            }
        };
        return dlg.ShowDialog() == true ? result : null;
    }

    /// <summary>拉取后端 /v1/config 回填运行时真相：API Key 是否已配置（可能由环境变量/
    /// 后端注入，本地 appsettings 未必有）、config.toml 是否损坏、实际生效的嵌入模型/LLM 配置。
    /// 不覆盖用户正在编辑的字段。
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
            OnPropertyChanged(nameof(ActiveBackendApiKeyConfigured));
            OnPropertyChanged(nameof(HasSavedApiKey));
            OnPropertyChanged(nameof(ApiKeyStatusText));

            // 批次 2：回填后端实际生效配置到状态卡
            ActiveBackendEmbedModel = cfg.EmbedModel;
            ActiveBackendLlmProvider = cfg.LlmProvider;
            ActiveBackendLlmModel = cfg.LlmModel;
            RebuildEffectiveConfigItems(cfg);
            IsBackendConfigLoaded = true;

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

    /// <summary>外部组件路径面板（poppler / 后端 Python / wheels / 模型缓存，
    /// 支持手动指定与「自动寻找可用配置」）。DI 未注册时为 null（向后兼容）。</summary>
    public ResourcePathPanelViewModel? ResourcePaths { get; }

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

    /// <summary>入库时自动跑 AI 整理（enrich + 可选 categorize + extract）。
    /// 关闭后只写元数据不调 LLM；dedup/consolidate 永不自动跑。</summary>
    public bool AutoCurateOnIngest
    {
        get => _autoCurateOnIngest;
        set => SetDirty(ref _autoCurateOnIngest, value);
    }

    /// <summary>Agent 模式（进阶，默认关闭）。开启后对话请求带 agentMode=true；
    /// 仍受后端 agent_mode_enabled 约束，后端关闭时服务端回落 RAG。</summary>
    public bool AgentModeEnabled
    {
        get => _agentModeEnabled;
        set => SetDirty(ref _agentModeEnabled, value);
    }

    /// <summary>全局默认回答模式：rag | agent | auto。与 Agent 总闸分离。</summary>
    public string DefaultChatMode
    {
        get => _defaultChatMode;
        set
        {
            var v = (value ?? "rag").Trim().ToLowerInvariant();
            if (v is not ("rag" or "agent" or "auto"))
            {
                v = "rag";
            }
            SetDirty(ref _defaultChatMode, v);
        }
    }

    /// <summary>启动时自动 ingest 的目录路径（空表示不自动导入）。</summary>
    private bool _longformEnabled;
    private int _longformMaxSections = 8;
    private int _longformMaxChars = 12000;
    private string _searchProvider = "builtin";
    private string _searchProviderApiKey = "";
    private string? _searchProviderEndpoint;

    /// <summary>长文大纲编排开关（交付轨专用）。</summary>
    public bool LongformEnabled
    {
        get => _longformEnabled;
        set => SetDirty(ref _longformEnabled, value);
    }

    /// <summary>长文最大章节数（2-16）。</summary>
    public int LongformMaxSections
    {
        get => _longformMaxSections;
        set => SetDirty(ref _longformMaxSections, Math.Clamp(value, 2, 16));
    }

    /// <summary>长文总字数上限。</summary>
    public int LongformMaxChars
    {
        get => _longformMaxChars;
        set => SetDirty(ref _longformMaxChars, Math.Clamp(value, 1000, 100000));
    }

    /// <summary>联网搜索 Provider：builtin / tavily / bocha / serpapi。</summary>
    public string SearchProvider
    {
        get => _searchProvider;
        set => SetDirty(ref _searchProvider, string.IsNullOrWhiteSpace(value) ? "builtin" : value.Trim().ToLowerInvariant());
    }

    /// <summary>搜索 Provider API Key（明文仅本机内存；保存后推后端不落本地日志）。</summary>
    public string SearchProviderApiKey
    {
        get => _searchProviderApiKey;
        set => SetDirty(ref _searchProviderApiKey, value ?? "");
    }

    /// <summary>搜索 Provider 自定义端点（http-json 用）。</summary>
    public string? SearchProviderEndpoint
    {
        get => _searchProviderEndpoint;
        set => SetDirty(ref _searchProviderEndpoint, value);
    }
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
        set
        {
            if (SetDirty(ref _llmProvider, value))
            {
                ScheduleLlmAutoApply();
            }
        }
    }

    /// <summary>API Key（内存中持明文，落盘时经 DPAPI 加密；留空保存 = 保留原值）。</summary>
    public string? LlmApiKey
    {
        get => _llmApiKey;
        set
        {
            if (SetDirty(ref _llmApiKey, value))
            {
                if (!string.IsNullOrWhiteSpace(value))
                {
                    // 重新输入即取消「清除」请求
                    _clearApiKeyRequested = false;
                }
                ScheduleLlmAutoApply();
            }
        }
    }

    /// <summary>是否已配置 API Key（控制「清除 Key」按钮可用性）。
    /// 本地 appsettings 有 key，或后端报告已配置（环境变量/手动注入）均视为已配置，
    /// 保证「后端有 key 但本地快照没有」时用户仍能清除。</summary>
    public bool HasSavedApiKey => !string.IsNullOrWhiteSpace(_savedApiKeyAtLoad) || _backendApiKeyConfigured;

    /// <summary>后端当前实际生效的 API Key 状态（来自 GET /v1/config）。</summary>
    public bool ActiveBackendApiKeyConfigured => _backendApiKeyConfigured;

    // ── 批次 2：配置状态透明化属性 ──
    /// <summary>后端实际生效的嵌入模型名（来自 GET /v1/config，状态卡展示）。</summary>
    public string? ActiveBackendEmbedModel
    {
        get => _activeBackendEmbedModel;
        private set => SetProperty(ref _activeBackendEmbedModel, value);
    }

    /// <summary>后端实际生效的 LLM 提供商（来自 GET /v1/config，状态卡展示）。</summary>
    public string? ActiveBackendLlmProvider
    {
        get => _activeBackendLlmProvider;
        private set => SetProperty(ref _activeBackendLlmProvider, value);
    }

    /// <summary>后端实际生效的 LLM 模型名（来自 GET /v1/config，状态卡展示）。</summary>
    public string? ActiveBackendLlmModel
    {
        get => _activeBackendLlmModel;
        private set => SetProperty(ref _activeBackendLlmModel, value);
    }

    /// <summary>是否已成功从后端加载配置（状态卡可见性）。</summary>
    public bool IsBackendConfigLoaded
    {
        get => _isBackendConfigLoaded;
        private set => SetProperty(ref _isBackendConfigLoaded, value);
    }

    /// <summary>FC-04：后端「当前生效配置」明细列表（用户可确认多项参数是否真生效）。</summary>
    public System.Collections.ObjectModel.ObservableCollection<EffectiveConfigItem> EffectiveConfigItems { get; } = new();

    /// <summary>用 GET /v1/config 回填生效配置清单（不覆盖用户正在编辑的字段）。</summary>
    internal void RebuildEffectiveConfigItems(BackendConfig cfg)
    {
        EffectiveConfigItems.Clear();
        void Add(string cat, string name, string value, string hint)
            => EffectiveConfigItems.Add(new EffectiveConfigItem { Category = cat, Name = name, Value = value, EffectHint = hint });

        var realtime = "实时（运行时）";
        var restart = "重启后端后生效";
        var nextBoot = "下次启动兜底";

        Add("嵌入", "模型", cfg.EmbedModel ?? "—", restart);
        if (!string.IsNullOrWhiteSpace(cfg.EmbedModelPath))
            Add("嵌入", "本地路径", cfg.EmbedModelPath!, restart);
        Add("嵌入", "批大小", cfg.EmbedBatchSize.ToString(), restart);

        Add("分块", "MaxTokens", cfg.ChunkMaxTokens.ToString(), restart);
        Add("分块", "Min/Overlap/MaxChars", $"{cfg.ChunkMinChars}/{cfg.ChunkOverlapChars}/{cfg.ChunkMaxChars}", restart);

        Add("检索", "Top-K / RRF-k", $"{cfg.SearchTopK} / {cfg.RrfK}", realtime);
        Add("检索", "重排", cfg.RerankEnabled ? $"{cfg.RerankModel} (recall={cfg.RerankRecall})" : "关闭", restart);

        Add("LLM", "Provider / Model", $"{cfg.LlmProvider} / {(string.IsNullOrWhiteSpace(cfg.LlmModel) ? "—" : cfg.LlmModel)}", realtime);
        Add("LLM", "Base URL", string.IsNullOrWhiteSpace(cfg.LlmBaseUrl) ? "（默认）" : cfg.LlmBaseUrl!, realtime);
        Add("LLM", "温度 / MaxTokens", $"{cfg.LlmTemperature} / {cfg.LlmMaxTokens}", realtime);
        Add("LLM", "超时(秒)", cfg.LlmTimeout > 0 ? cfg.LlmTimeout.ToString("0.#") : "默认", realtime);
        Add("LLM", "API Key", cfg.LlmApiKeyConfigured ? "已配置" : "未配置", realtime);

        Add("RAG", "Top-K / MinScore / Mode", $"{cfg.RagTopK} / {cfg.RagMinScore} / {cfg.RagMode}", realtime);
        Add("RAG", "历史 token 预算", cfg.RagMaxHistoryTokens > 0 ? cfg.RagMaxHistoryTokens.ToString() : "不限", realtime);
        Add("RAG", "自定义系统提示词", string.IsNullOrWhiteSpace(cfg.RagSystemPrompt) ? "（内置默认）" : "已自定义", realtime);

        Add("文件监控", "目录数 / 去抖", $"{cfg.WatchPaths?.Count ?? 0} / {cfg.WatchDebounceSeconds:0.#}s", restart);
        Add("联网", "搜索超时(秒)", cfg.WebSearchTimeout.ToString("0.#"), realtime);
        Add("整理", "入库自动 AI 整理", cfg.AutoCurateOnIngest ? "开启" : "关闭", realtime);

        Add("Agent（进阶）", "模式", cfg.AgentModeEnabled ? "已启用" : "未启用（默认）", "需后端 agent_mode_enabled=true");
        Add("长文编排", "开关/章节/字数", $"{(cfg.LongformEnabled ? "开" : "关")} / {cfg.LongformMaxSections} / {cfg.LongformMaxChars}", "交付轨专用");
        Add("搜索 Provider", "提供商", cfg.SearchProvider, cfg.SearchProviderApiKeyConfigured ? "Key 已配置" : "未配置 Key（回落 builtin）");
        Add("Agent（进阶）", "默认回答模式", cfg.ChatModeDefault ?? "rag", realtime);
        Add("Agent（预留）", "工作区写入策略", cfg.AgentFileWritePolicy, "进阶能力启用后生效");

        OnPropertyChanged(nameof(EffectiveConfigItems));
    }

    /// <summary>API Key 状态徽章文案（"已配置 ✓" / "未配置"）。</summary>
    public string ApiKeyStatusText => HasSavedApiKey ? "已配置 ✓" : "未配置";

    /// <summary>是否显示 API Key 清除确认对话框。</summary>
    public bool ShowApiKeyClearConfirm
    {
        get => _showApiKeyClearConfirm;
        set => SetProperty(ref _showApiKeyClearConfirm, value);
    }

    /// <summary>上次保存前的后端连接与启动超时快照（用于重启类字段变更检测）。</summary>
    private string? _savedBackendUrlAtLoad;
    private int _savedStartupTimeoutSecAtLoad;
    private string? _savedBackendCommandAtLoad;

    /// <summary>GitHub Token（联网搜索 GitHub 通道用；可选，内存持明文，落盘 DPAPI 加密）。
    /// 每个用户填自己的 Token，随对话请求携带，后端不写全局配置。</summary>
    public string? GithubToken
    {
        get => _githubToken;
        set
        {
            if (SetDirty(ref _githubToken, value) && !string.IsNullOrWhiteSpace(value))
            {
                // 重新输入即取消「清除」请求
                _clearGithubTokenRequested = false;
            }
        }
    }

    /// <summary>是否已配置 GitHub Token（控制「清除」按钮可用性）。</summary>
    public bool HasSavedGithubToken => !string.IsNullOrWhiteSpace(_savedGithubTokenAtLoad);

    /// <summary>API 基础地址（如 https://api.deepseek.com/v1）。</summary>
    public string? LlmBaseUrl
    {
        get => _llmBaseUrl;
        set
        {
            if (SetDirty(ref _llmBaseUrl, value))
            {
                ScheduleLlmAutoApply();
            }
        }
    }

    /// <summary>模型名（如 deepseek-chat、gpt-4o-mini、llama3.2）。</summary>
    public string LlmModel
    {
        get => _llmModel;
        set
        {
            if (SetDirty(ref _llmModel, value))
            {
                SyncSelectedModelCandidate();
                ScheduleLlmAutoApply();
            }
        }
    }

    /// <summary>把当前模型名（LlmModel）同步为模型下拉的选中项：命中候选则直接选中，
    /// 未命中则先补一个候选再选中。
    /// 背景：下拉的 Text 绑定 LlmModel、SelectedItem 绑定 SelectedModelCandidate，
    /// 两者原本互不同步，导致代码赋值 LlmModel 后下拉没有选中态高亮，
    /// 表现为「看不出当前用的是哪个模型」。
    /// 构造期与 setter 共用；只写 SelectedModelCandidate（SetProperty，不标脏），
    /// 因此构造函数调用不会影响「加载后 IsDirty=false」的既有约定。</summary>
    private void SyncSelectedModelCandidate()
    {
        var name = _llmModel?.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            SelectedModelCandidate = null;
            return;
        }
        var hit = LlmModels.FirstOrDefault(m => string.Equals(m.Name, name, StringComparison.OrdinalIgnoreCase));
        if (hit is null)
        {
            hit = new LlmModelItem(name);
            LlmModels.Insert(0, hit);
        }
        SelectedModelCandidate = hit;
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

    /// <summary>LLM 调用/流式空闲超时（秒）。慢网/大模型建议 300+。</summary>
    public double LlmTimeoutSec
    {
        get => _llmTimeoutSec;
        set => SetDirty(ref _llmTimeoutSec, value);
    }

    /// <summary>联网搜索总预算（秒）。慢网/反爬环境建议 24~40。</summary>
    public double WebSearchTimeoutSec
    {
        get => _webSearchTimeoutSec;
        set => SetDirty(ref _webSearchTimeoutSec, value);
    }

    /// <summary>自建/自选 SearXNG 实例地址；空 = 内置公有实例。</summary>
    public string? WebSearchSearxngUrl
    {
        get => _webSearchSearxngUrl;
        set => SetDirty(ref _webSearchSearxngUrl, value);
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

    /// <summary>RAG 问答模式（"strict" = 严格知识库模式；"hybrid" = 混合常识增强模式）。</summary>
    public string RagMode
    {
        get => _ragMode;
        set => SetDirty(ref _ragMode, value);
    }

    /// <summary>是否启用检索后重排（Reranker / cross-encoder 精排），显著提升知识检索相关性。</summary>
    public bool RerankEnabled
    {
        get => _rerankEnabled;
        set => SetDirty(ref _rerankEnabled, value);
    }

    /// <summary>重排模型名（fastembed TextRanking 支持列表中的模型）。</summary>
    public string RerankModel
    {
        get => _rerankModel;
        set => SetDirty(ref _rerankModel, value);
    }

    /// <summary>送入重排器的候选数上限（默认 20）。</summary>
    public int RerankRecall
    {
        get => _rerankRecall;
        set => SetDirty(ref _rerankRecall, value);
    }

    /// <summary>是否显示提示用户平滑重启后端的 Banner。</summary>
    public bool ShowRestartBanner
    {
        get => _showRestartBanner;
        set => SetProperty(ref _showRestartBanner, value);
    }

    /// <summary>平滑重启 Banner 的提示说明。</summary>
    public string RestartBannerText
    {
        get => _restartBannerText;
        set => SetProperty(ref _restartBannerText, value);
    }

    /// <summary>「获取模型列表」拉取到的可用模型（设置页模型下拉候选；含上下文窗口等元数据）。
    /// 点击「获取模型列表」后自动全量导入到该服务商，无需逐个添加。</summary>
    public System.Collections.ObjectModel.ObservableCollection<LlmModelItem> LlmModels { get; } = new();

    private LlmModelItem? _selectedModelCandidate;

    /// <summary>模型下拉当前选中的候选（用于「移除模型」）。</summary>
    public LlmModelItem? SelectedModelCandidate
    {
        get => _selectedModelCandidate;
        set
        {
            if (SetProperty(ref _selectedModelCandidate, value) && value is not null
                && !string.Equals(LlmModel, value.Name, StringComparison.OrdinalIgnoreCase))
            {
                LlmModel = value.Name;
            }
        }
    }

    public bool IsTestingModels
    {
        get => _isTestingModels;
        private set => SetProperty(ref _isTestingModels, value);
    }

    [RelayCommand]
    private void SetDefaultModel(LlmModelItem? model)
    {
        if (model is null) return;
        SelectedModelCandidate = model;
        LlmModel = model.Name;
        _appSettings.LlmProvider = LlmProvider;
        _appSettings.LlmBaseUrl = LlmBaseUrl;
        _appSettings.LlmModel = model.Name;
        _appSettings.LlmAvailableModels = LlmModels.Select(m => m.Name).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        _appSettings.Save();
        StatusMessage = $"已将「{model.Name}」设为当前服务商默认模型，已持久化保存。";
    }

    [RelayCommand]
    private async Task TestModelAsync(LlmModelItem? model)
    {
        if (model is null || IsTestingModels) return;
        await TestModelCoreAsync(model);
    }

    [RelayCommand]
    private async Task TestAllModelsAsync()
    {
        if (IsTestingModels || LlmModels.Count == 0) return;

        IsTestingModels = true;
        StatusMessage = $"正在逐个测试 {LlmModels.Count} 个模型…";
        try
        {
            foreach (var model in LlmModels.ToList())
            {
                await TestModelCoreAsync(model);
            }

            var passed = LlmModels.Count(m => m.TestOk == true);
            StatusMessage = $"模型测试完成：{passed}/{LlmModels.Count} 可用";
        }
        finally
        {
            IsTestingModels = false;
        }
    }

    private async Task TestModelCoreAsync(LlmModelItem model)
    {
        model.SetTestState(true, null, "测试中…");
        try
        {
            var result = await _apiService.LlmTestAsync(new LlmTestRequest
            {
                Provider = string.IsNullOrWhiteSpace(LlmProvider) ? null : LlmProvider.Trim(),
                ApiKey = string.IsNullOrWhiteSpace(LlmApiKey) ? null : LlmApiKey.Trim(),
                BaseUrl = string.IsNullOrWhiteSpace(LlmBaseUrl) ? null : LlmBaseUrl.Trim(),
                Model = model.Name,
                Timeout = 20,
            });

            var status = result.Ok
                ? $"可用 · {result.ElapsedMs} ms"
                : $"失败 · {result.Error ?? "未知错误"}";
            model.SetTestState(false, result.Ok, status);
        }
        catch (Exception ex)
        {
            model.SetTestState(false, false, $"失败 · {ex.Message}");
        }
    }

    /// <summary>把当前模型输入（LlmModel）加入该服务商的模型候选列表（去重；不落盘，保存服务商时生效）。</summary>
    [RelayCommand]
    private void AddModelToProvider()
    {
        var model = LlmModel?.Trim();
        if (string.IsNullOrWhiteSpace(model))
        {
            StatusMessage = "请先在模型名称中输入要添加的模型";
            _notifications.Warning("请先在模型名称中输入要添加的模型", "添加模型");
            return;
        }
        if (LlmModels.Any(m => string.Equals(m.Name, model, StringComparison.OrdinalIgnoreCase)))
        {
            StatusMessage = $"模型「{model}」已在候选列表中";
            return;
        }
        LlmModels.Add(new LlmModelItem(model));
        SelectedModelCandidate = LlmModels.Last();
        StatusMessage = $"已添加模型「{model}」到候选列表（保存服务商时生效）";
        DebugLog.Info($"添加模型到服务商候选: {model}", "Settings");
    }

    /// <summary>从模型候选列表中移除指定模型（不落盘，保存服务商时生效）。
    /// 参数为列表项「✕」按钮传入的 LlmModelItem；为空时回退到下拉选中项。</summary>
    [RelayCommand]
    private void RemoveModelFromProvider(LlmModelItem? item)
    {
        var targetName = (item?.Name ?? SelectedModelCandidate?.Name)?.Trim();
        if (string.IsNullOrWhiteSpace(targetName))
        {
            StatusMessage = "请先在模型下拉中选中要移除的模型";
            _notifications.Warning("请先在模型下拉中选中要移除的模型", "移除模型");
            return;
        }
        var toRemove = LlmModels.FirstOrDefault(m => string.Equals(m.Name, targetName, StringComparison.OrdinalIgnoreCase));
        if (toRemove is null)
        {
            return;
        }
        LlmModels.Remove(toRemove);
        if (string.Equals(LlmModel?.Trim(), targetName, StringComparison.OrdinalIgnoreCase))
        {
            LlmModel = LlmModels.FirstOrDefault()?.Name ?? "";
        }
        SelectedModelCandidate = null;
        StatusMessage = $"已移除模型「{targetName}」（保存服务商时生效）";
        DebugLog.Info($"移除服务商候选模型: {targetName}", "Settings");
    }

    /// <summary>该服务商当前模型列表是否为空（用于下方列表空态提示）。</summary>
    public bool HasModels => LlmModels.Count > 0;

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
                if (!_isInitializing)
                {
                    ApplyPreset(_selectedPreset);
                }
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
                LlmModels.Add(new LlmModelItem(m));
            }
        }

        // 名称自动默认：直接取预设显示名（用户可改；custom 预设不自动填）
        ProfileNameInput = preset.DisplayName;

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

    // --- 备份与诊断包（Phase 6：普通用户可恢复）---
    [RelayCommand]
    public async Task CreateBackupAsync()
    {
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            Title = "保存 DocMind 知识库备份",
            Filter = "DocMind 备份 (*.docmind.zip)|*.docmind.zip|Zip 文件 (*.zip)|*.zip",
            FileName = $"docmind-{DateTime.Now:yyyyMMdd-HHmmss}.docmind.zip",
        };
        if (dialog.ShowDialog() != true)
            return;
        try
        {
            StatusMessage = "正在创建知识库备份...";
            var result = await _apiService.CreateBackupAsync(dialog.FileName);
            StatusMessage = $"备份完成：{result.Path}";
            _notifications.Success($"知识库备份已保存\n{result.Path}", "备份完成");
        }
        catch (Exception ex)
        {
            StatusMessage = $"备份失败：{ex.Message}";
            _notifications.Error($"创建备份失败：{ex.Message}");
        }
    }

    [RelayCommand]
    public async Task RestoreBackupAsync()
    {
        var dialog = new Microsoft.Win32.OpenFileDialog
        {
            Title = "选择 DocMind 知识库备份",
            Filter = "DocMind 备份 (*.docmind.zip;*.zip)|*.docmind.zip;*.zip",
        };
        if (dialog.ShowDialog() != true)
            return;
        var confirm = System.Windows.MessageBox.Show(
            "恢复会替换当前知识库。后端会自动保留恢复前的数据库副本，是否继续？",
            "确认恢复知识库", System.Windows.MessageBoxButton.YesNo, System.Windows.MessageBoxImage.Warning);
        if (confirm != System.Windows.MessageBoxResult.Yes)
            return;
        try
        {
            StatusMessage = "正在恢复知识库...";
            var result = await _apiService.RestoreBackupAsync(dialog.FileName);
            StatusMessage = "知识库恢复完成，请重新检测索引状态";
            _notifications.Success($"恢复完成。原数据库副本：{result.PreviousBackup ?? "无"}", "恢复完成");
        }
        catch (Exception ex)
        {
            StatusMessage = $"恢复失败：{ex.Message}";
            _notifications.Error($"恢复知识库失败：{ex.Message}");
        }
    }

    [RelayCommand]
    public async Task CreateDiagnosticBundleAsync()
    {
        var dialog = new Microsoft.Win32.SaveFileDialog
        {
            Title = "保存 DocMind 诊断包",
            Filter = "Zip 文件 (*.zip)|*.zip",
            FileName = $"docmind-diagnostics-{DateTime.Now:yyyyMMdd-HHmmss}.zip",
        };
        if (dialog.ShowDialog() != true)
            return;
        try
        {
            StatusMessage = "正在打包脱敏诊断信息...";
            var result = await _apiService.CreateDiagnosticBundleAsync(dialog.FileName, includeLogs: false);
            StatusMessage = $"诊断包已保存：{result.Path}";
            _notifications.Success($"诊断包已保存\n{result.Path}", "诊断包完成");
        }
        catch (Exception ex)
        {
            StatusMessage = $"诊断包失败：{ex.Message}";
            _notifications.Error($"创建诊断包失败：{ex.Message}");
        }
    }

    // ===================== 运行依赖就绪状态（/v1/system/dependencies）=====================

    private DependenciesStatus? _dependencies;
    private bool _isDependenciesLoading;

    /// <summary>运行依赖就绪状态聚合（GPU/OCR/嵌入模型缓存/Poppler）。</summary>
    public DependenciesStatus? Dependencies
    {
        get => _dependencies;
        private set
        {
            if (SetProperty(ref _dependencies, value))
            {
                OnPropertyChanged(nameof(HasDependencies));
                OnPropertyChanged(nameof(DependenciesSummaryText));
                OnPropertyChanged(nameof(DependencyCheckSummary));
            }
        }
    }

    public bool HasDependencies => Dependencies is not null;

    /// <summary>按依赖项逐行渲染的检查项（GPU/OCR/模型缓存/PDF）。</summary>
    public IReadOnlyList<DependencyCheckItem> DependencyCheckSummary => BuildDependencyChecks();

    public bool IsDependenciesLoading
    {
        get => _isDependenciesLoading;
        private set => SetProperty(ref _isDependenciesLoading, value);
    }

    /// <summary>一行摘要（全绿/警告），供通知与状态栏复用。</summary>
    public string DependenciesSummaryText
    {
        get
        {
            if (Dependencies is null)
                return "";
            var checks = BuildDependencyChecks();
            var ok = checks.Count(c => c.Ok);
            return $"{ok}/{checks.Count} 项依赖就绪";
        }
    }

    private IReadOnlyList<DependencyCheckItem> BuildDependencyChecks()
    {
        var d = Dependencies;
        if (d is null)
            return [];

        return
        [
            new DependencyCheckItem("GPU 加速", d.GpuAvailable,
                d.GpuAvailable ? $"{d.GpuProvider ?? "GPU"}（{d.GpuName ?? "-"}）" : "未启用（CPU 推理可用）"),
            new DependencyCheckItem("OCR 文字识别", d.OcrAvailable,
                d.OcrAvailable ? "已安装（PaddleOCR）" : "未安装（导入扫描件/图片文档时可用）"),
            new DependencyCheckItem("嵌入模型缓存", d.ModelCached,
                d.ModelCached ? $"已缓存（{d.ModelName}）" : $"未缓存（{d.ModelName}，首次使用自动下载）"),
            new DependencyCheckItem("PDF 转换 (Poppler)", d.PopplerAvailable,
                d.PopplerAvailable ? "已就绪" : "缺失（部分 PDF 渲染/转换受限）"),
        ];
    }

    /// <summary>拉取运行依赖就绪状态（设置页「算力与体检」首次进入/手动刷新）。</summary>
    [RelayCommand]
    public async Task RefreshDependenciesAsync()
    {
        if (IsDependenciesLoading)
            return;

        IsDependenciesLoading = true;
        try
        {
            Dependencies = await _apiService.GetDependenciesAsync();
            DebugLog.Info($"依赖状态拉取完成: {DependenciesSummaryText}", "Settings");
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"依赖状态拉取失败: {ex.GetType().Name}: {ex.Message}", "Settings");
        }
        finally
        {
            IsDependenciesLoading = false;
        }
    }

    // ===================== 嵌入模型下载（/v1/system/download-model）=====================

    private bool _isDownloadingModel;
    private double _modelDownloadProgress;
    private string _modelDownloadStatus = "";
    private string? _modelDownloadResult;

    public bool IsDownloadingModel
    {
        get => _isDownloadingModel;
        private set
        {
            if (SetProperty(ref _isDownloadingModel, value))
            {
                DownloadModelCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>模型下载进度 0.0 ~ 1.0（SSE 字节进度）。</summary>
    public double ModelDownloadProgress
    {
        get => _modelDownloadProgress;
        private set => SetProperty(ref _modelDownloadProgress, value);
    }

    /// <summary>模型下载百分比文本（进度条 ToolTip）。</summary>
    public string ModelDownloadProgressText => $"{(int)(ModelDownloadProgress * 100)}%";

    /// <summary>模型下载状态文本（进行中/完成/失败）。</summary>
    public string ModelDownloadStatus
    {
        get => _modelDownloadStatus;
        private set
        {
            if (SetProperty(ref _modelDownloadStatus, value))
            {
                OnPropertyChanged(nameof(ModelDownloadStatusDisplay));
            }
        }
    }

    /// <summary>下载状态显示文本（未开始时给引导文案）。</summary>
    public string ModelDownloadStatusDisplay
        => string.IsNullOrWhiteSpace(ModelDownloadStatus)
            ? "尚未下载过（首次使用嵌入时会自动下载）"
            : ModelDownloadStatus;

    /// <summary>已下载模型快照目录（用于提示重启后端生效）。</summary>
    public string? ModelDownloadResult
    {
        get => _modelDownloadResult;
        private set => SetProperty(ref _modelDownloadResult, value);
    }

    public bool CanDownloadModel => !IsDownloadingModel;

    /// <summary>下载当前选择的嵌入模型（离线包/手动补缓存场景）。</summary>
    [RelayCommand(CanExecute = nameof(CanDownloadModel))]
    public async Task DownloadModelAsync()
    {
        if (IsDownloadingModel)
            return;

        IsDownloadingModel = true;
        ModelDownloadProgress = 0;
        ModelDownloadStatus = "正在连接后端…";
        ModelDownloadResult = null;
        OnPropertyChanged(nameof(ModelDownloadProgressText));
        try
        {
            void ApplyDownloadProgress(DownloadProgressFrame f)
            {
                // 字节优先，退化到文件进度；避免回跳
                var p = f.Progress;
                ModelDownloadProgress = Math.Max(ModelDownloadProgress, Math.Min(1.0, p));
                OnPropertyChanged(nameof(ModelDownloadProgressText));
                ModelDownloadStatus = f.TotalBytes > 0
                    ? $"下载中… {FormatBytes(f.DownloadedBytes)} / {FormatBytes(f.TotalBytes)}"
                    : $"下载中… 文件 {f.DownloadedFiles}/{f.TotalFiles}";
            }

            var progress = new Progress<DownloadProgressFrame>(f =>
            {
                var app = System.Windows.Application.Current;
                if (app?.Dispatcher != null && !app.Dispatcher.CheckAccess())
                {
                    app.Dispatcher.InvokeAsync(() => ApplyDownloadProgress(f));
                }
                else
                {
                    ApplyDownloadProgress(f);
                }
            });

            var snapPath = await _apiService.DownloadModelAsync(
                modelName: null, progress: progress);

            ModelDownloadProgress = 1.0;
            OnPropertyChanged(nameof(ModelDownloadProgressText));
            ModelDownloadResult = snapPath;
            ModelDownloadStatus = "✅ 模型下载完成（重启后端后加载新模型）";
            _notifications.Success($"嵌入模型下载完成！重启后端后生效。", "模型下载");
            DebugLog.Info($"嵌入模型下载完成: snapshot={snapPath}", "Settings");
        }
        catch (OperationCanceledException)
        {
            ModelDownloadStatus = "下载已取消";
        }
        catch (Exception ex)
        {
            ModelDownloadStatus = $"❌ 模型下载失败: {ex.Message}";
            DebugLog.Error($"模型下载失败: {ex}", "Settings", ex);
            _notifications.Error($"模型下载失败：{ex.Message}");
        }
        finally
        {
            IsDownloadingModel = false;
        }
    }

    private static string FormatBytes(long bytes)
    {
        const double mb = 1024.0 * 1024.0;
        return bytes >= mb ? $"{bytes / mb:F1} MB" : $"{bytes / 1024.0 / 1024.0:F2} MB";
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
        if (changed && !_isInitializing)
        {
            IsDirty = true;
        }
        return changed;
    }

    private bool CanSave => IsDirty;

    // ── LLM 连接字段「输入即生效」──────────────────────────────────────────
    // 提供商/Key/地址/模型变化后防抖 1.2s 推送后端运行时（persist=false，不落盘）：
    // 输入即可测试、调用（对话/RAG 走后端运行时配置），点「保存」才持久化到本地+config.toml。

    /// <summary>LLM 连接字段变化后重置防抖计时器（表单回填/档案应用/构造初始化期间跳过）。</summary>
    private void ScheduleLlmAutoApply()
    {
        if (_isInitializing || _isBackfillingLlmForm || IsApplyingProfile)
        {
            return;
        }
        _llmAutoApplyTimer ??= CreateLlmAutoApplyTimer();
        _llmAutoApplyTimer.Stop();
        _llmAutoApplyTimer.Start();
    }

    private System.Windows.Threading.DispatcherTimer CreateLlmAutoApplyTimer()
    {
        var timer = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromMilliseconds(1200) };
        timer.Tick += async (_, _) =>
        {
            timer.Stop();
            await AutoApplyLlmConnectionAsync();
        };
        return timer;
    }

    /// <summary>把当前 LLM 连接输入推送到后端运行时（只推非空字段；空 = 不修改，
    /// 避免输入中途误清后端已有配置——清除 Key/地址仍走显式清除+保存流程）。</summary>
    private async Task AutoApplyLlmConnectionAsync()
    {
        if (_isAutoApplyingLlm || IsApplyingProfile)
        {
            if (_isAutoApplyingLlm)
            {
                _llmAutoApplyPending = true;
            }
            return;
        }

        string? pushProvider = string.IsNullOrWhiteSpace(LlmProvider) ? null : LlmProvider.Trim();
        var providerChanged = !string.Equals(pushProvider, _appliedLlmProvider, StringComparison.OrdinalIgnoreCase);
        string? pushApiKey = string.IsNullOrWhiteSpace(LlmApiKey) ? (providerChanged ? "" : null) : LlmApiKey!.Trim();
        string? pushBaseUrl = string.IsNullOrWhiteSpace(LlmBaseUrl) ? (providerChanged ? "" : null) : LlmBaseUrl.Trim();
        string? pushModel = string.IsNullOrWhiteSpace(LlmModel) ? (providerChanged ? "" : null) : LlmModel.Trim();
        if (pushProvider == _appliedLlmProvider && pushApiKey == _appliedLlmApiKey
            && pushBaseUrl == _appliedLlmBaseUrl && pushModel == _appliedLlmModel)
        {
            return; // 与后端当前生效值一致，无需推送
        }

        _isAutoApplyingLlm = true;
        try
        {
            await _apiService.UpdateConfigAsync(new BackendConfigUpdate
            {
                LlmProvider = pushProvider,
                LlmApiKey = pushApiKey,
                LlmBaseUrl = pushBaseUrl,
                LlmModel = pushModel,
                Persist = false,
            });
            _appliedLlmProvider = pushProvider;
            _appliedLlmApiKey = pushApiKey;
            _appliedLlmBaseUrl = pushBaseUrl;
            _appliedLlmModel = pushModel;
            if (pushApiKey != null)
            {
                _backendApiKeyConfigured = true;
                OnPropertyChanged(nameof(HasSavedApiKey));
                OnPropertyChanged(nameof(ApiKeyStatusText));
            }
            StatusMessage = "LLM 连接配置已即时生效（点「保存」后重启仍保留）";
            DebugLog.Info(
                $"LLM 连接已即时生效: provider={pushProvider ?? "-"} model={pushModel ?? "-"} " +
                $"baseUrl={(string.IsNullOrEmpty(pushBaseUrl) ? "-" : pushBaseUrl)}",
                "Settings");
        }
        catch (Exception ex)
        {
            // 即时推送失败不弹窗（后端未就绪时输入会频繁触发）；保存仍会全量推送兜底
            DebugLog.Warn($"LLM 连接即时生效失败（保存后仍会生效）: {ex.Message}", "Settings");
        }
        finally
        {
            _isAutoApplyingLlm = false;
            if (_llmAutoApplyPending && !IsApplyingProfile)
            {
                _llmAutoApplyPending = false;
                await AutoApplyLlmConnectionAsync();
            }
        }
    }

    /// <summary>把「已推送后端」快照同步为当前输入（构造加载与保存成功后调用，避免防抖重复推送）。</summary>
    private void SyncLlmAppliedSnapshot()
    {
        _appliedLlmProvider = string.IsNullOrWhiteSpace(_llmProvider) ? null : _llmProvider.Trim();
        _appliedLlmApiKey = string.IsNullOrWhiteSpace(_llmApiKey) ? null : _llmApiKey!.Trim();
        _appliedLlmBaseUrl = string.IsNullOrWhiteSpace(_llmBaseUrl) ? null : _llmBaseUrl.Trim();
        _appliedLlmModel = string.IsNullOrWhiteSpace(_llmModel) ? null : _llmModel.Trim();
    }

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
            var llmProviderChanged = !string.Equals(
                LlmProvider?.Trim(), _appSettings.LlmProvider?.Trim(), StringComparison.OrdinalIgnoreCase);
            
            if (_appSettings.LlmKeyDecryptFailed && !hasApiKeyInput && !_clearApiKeyRequested)
            {
                _notifications.Error("API 密钥解密失败，为防止原密钥丢失，请重新输入密钥或点击清除后再保存。", "需要操作");
                StatusMessage = "保存中止：需要处理 API 密钥";
                return;
            }

            var effectiveApiKey = hasApiKeyInput
                ? LlmApiKey!.Trim()
                : (_clearApiKeyRequested || llmProviderChanged ? null : _appSettings.LlmApiKey);

            // GitHub Token 同语义：非空 → 使用输入值；留空 → 保留原值；点「清除」→ 置空
            var hasGithubTokenInput = !string.IsNullOrWhiteSpace(GithubToken);
            var effectiveGithubToken = hasGithubTokenInput
                ? GithubToken!.Trim()
                : (_clearGithubTokenRequested ? null : _appSettings.GithubToken);

            var oldEmbedModel = _appSettings.EmbedModel;
            var oldEmbedModelPath = _appSettings.EmbedModelPath;
            var oldChunkTokens = _appSettings.ChunkMaxTokens;
            var oldChunkMin = _appSettings.ChunkMinChars;
            var oldChunkOverlap = _appSettings.ChunkOverlapChars;
            var oldChunkMax = _appSettings.ChunkMaxChars;
            var oldBackendUrl = _appSettings.BackendUrl;
            var oldBackendCmd = _appSettings.BackendCommand;

            // 写回内存对象
            _appSettings.BackendUrl = BackendUrl;
            _appSettings.PollIntervalMs = PollIntervalMs;
            _appSettings.StartupTimeoutSec = StartupTimeoutSec;
            _appSettings.BackendCommand = BackendCommand;
            _appSettings.AutoStartBackend = AutoStartBackend;
            _appSettings.StopBackendOnExit = StopBackendOnExit;
            _appSettings.AutoCurateOnIngest = AutoCurateOnIngest;
            _appSettings.AgentModeEnabled = AgentModeEnabled;
            _appSettings.LongformEnabled = LongformEnabled;
            _appSettings.LongformMaxSections = LongformMaxSections;
            _appSettings.LongformMaxChars = LongformMaxChars;
            _appSettings.SearchProvider = SearchProvider;
            if (!string.IsNullOrEmpty(SearchProviderApiKey))
                _appSettings.SearchProviderApiKey = SearchProviderApiKey;
            _appSettings.SearchProviderEndpoint = SearchProviderEndpoint;
            _appSettings.DefaultChatMode = DefaultChatMode;
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
            _appSettings.GithubToken = effectiveGithubToken;
            _appSettings.LlmBaseUrl = LlmBaseUrl;
            _appSettings.LlmModel = LlmModel;
            // 镜像当前模型候选到持久化字段：对话页默认分组据此播种，
            // 使设置页点过「获取模型列表」后对话页能直接点选全部模型（重启后仍有效），
            // 无需用户再到对话页点一次刷新。语义为始终镜像设置页当前候选。
            _appSettings.LlmAvailableModels = LlmModels
                .Where(m => !string.IsNullOrWhiteSpace(m.Name))
                .Select(m => m.Name.Trim())
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
            _appSettings.LlmTemperature = LlmTemperature;
            _appSettings.LlmMaxTokens = LlmMaxTokens;
            _appSettings.LlmTimeoutSec = LlmTimeoutSec > 0 ? LlmTimeoutSec : 300;
            _appSettings.WebSearchTimeoutSec = WebSearchTimeoutSec > 0 ? WebSearchTimeoutSec : 36;
            _appSettings.WebSearchSearxngUrl = string.IsNullOrWhiteSpace(WebSearchSearxngUrl)
                ? null
                : WebSearchSearxngUrl.Trim();
            _appSettings.RagTopK = RagTopK;
            _appSettings.RagSystemPrompt = string.IsNullOrWhiteSpace(RagSystemPrompt) ? null : RagSystemPrompt;
            _appSettings.RagMaxHistoryTokens = RagMaxHistoryTokens;
            _appSettings.RagMode = RagMode;
            _appSettings.RerankEnabled = RerankEnabled;
            _appSettings.RerankModel = RerankModel;
            _appSettings.RerankRecall = RerankRecall;
            _appSettings.WatchPaths = WatchPaths.Where(p => !string.IsNullOrWhiteSpace(p)).Select(p => p.Trim()).ToList();
            _appSettings.WatchDebounceSeconds = WatchDebounceSeconds;
            _appSettings.DismissGpuWarning = _gpuWarning.Dismissed;

            // 落盘：AppSettings.Save() 是唯一持久化出口（全字段 camelCase，key 经 DPAPI 加密），
            // 避免双写盘路径（匿名对象/全对象、PascalCase/camelCase）互相覆盖。
            // 内存/AppSettings 单例仍持明文，供 BackendProcessService 注入环境变量与测试连接使用。
            _appSettings.Save();
            var settingsPath = AppSettings.ConfigPath;

            // 检测关键底层参数是否发生变更
            var criticalParamChanged = oldEmbedModel != EmbedModel
                || oldEmbedModelPath != EmbedModelPath
                || oldChunkTokens != ChunkMaxTokens
                || oldChunkMin != ChunkMinChars
                || oldChunkOverlap != ChunkOverlapChars
                || oldChunkMax != ChunkMaxChars
                || oldBackendUrl != BackendUrl
                || oldBackendCmd != BackendCommand;

            if (criticalParamChanged)
            {
                ShowRestartBanner = true;
                RestartBannerText = "💡 检测到嵌入模型或底层分块参数已变更，建议点击右侧【立即平滑重启后端】使新环境全面生效。";
            }

            // 推送到后端运行时配置（/v1/config），免重启生效；
            // 推送失败会显式警告（重启后端后环境变量仍会生效，见 BackendProcessService）。
            var pushFailed = false;
            try
            {
                // key：非空推明文；留空推 null（不修改后端已配置值）；「清除」推 "" 显式清除
                string? pushApiKey = hasApiKeyInput
                    ? LlmApiKey!.Trim()
                    : (_clearApiKeyRequested || llmProviderChanged ? "" : null);
                // base_url 清除语义：之前配置过、现在被清空 → 传 "" 显式清除；
                // 之前就没配置 → 传 null 不修改（避免误清后端手动配置的值）
                var pushBaseUrl = string.IsNullOrWhiteSpace(LlmBaseUrl)
                    ? (llmProviderChanged || !string.IsNullOrWhiteSpace(_savedBaseUrlAtLoad) ? "" : null)
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
                        ? (llmProviderChanged || !string.IsNullOrWhiteSpace(_savedModelAtLoad) ? "" : null)
                        : LlmModel.Trim(),
                    LlmTemperature = LlmTemperature,
                    LlmMaxTokens = LlmMaxTokens,
                    LlmTimeout = (float)(LlmTimeoutSec > 0 ? LlmTimeoutSec : 300),
                    WebSearchTimeout = (float)(WebSearchTimeoutSec > 0 ? WebSearchTimeoutSec : 36),
                    RagTopK = RagTopK,
                    RagMode = RagMode,
                    RerankEnabled = RerankEnabled,
                    RerankModel = string.IsNullOrWhiteSpace(RerankModel) ? null : RerankModel.Trim(),
                    RerankRecall = RerankRecall,
                    RagSystemPrompt = string.IsNullOrWhiteSpace(RagSystemPrompt)
                        ? (string.IsNullOrWhiteSpace(_savedRagSystemPromptAtLoad) ? null : "")
                        : RagSystemPrompt.Trim(),
                    RagMaxHistoryTokens = RagMaxHistoryTokens,
                    WatchPaths = WatchPaths.Where(p => !string.IsNullOrWhiteSpace(p)).Select(p => p.Trim()).ToList(),
                    WatchDebounceSeconds = WatchDebounceSeconds,
                    AutoCurateOnIngest = AutoCurateOnIngest,
                    // Agent 模式必须同步到后端，否则客户端勾选后服务端仍 agent_mode_enabled=false 回落 RAG
                    AgentModeEnabled = AgentModeEnabled,
                    AgentNativeToolCalling = true,
                    AgentFileWritePolicy = "session_allow",
                    ChatModeDefault = DefaultChatMode,
                    LongformEnabled = LongformEnabled,
                    LongformMaxSections = LongformMaxSections,
                    LongformMaxChars = LongformMaxChars,
                    SearchProvider = SearchProvider,
                    SearchProviderApiKey = string.IsNullOrEmpty(SearchProviderApiKey) ? null : SearchProviderApiKey,
                    SearchProviderEndpoint = SearchProviderEndpoint,
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
                ShowRestartBanner = true;
                RestartBannerText = "⚠️ 后端参数即时推送异常，请点击右侧【立即平滑重启后端】通过环境变量重新加载最新配置。";
                DebugLog.Warn($"后端参数推送失败（重启后端后仍会生效）: {ex.Message}", "Settings");
                _notifications.Warning(
                    $"后端推送失败：{ex.Message}\n配置已保存到本地，重启后端后将通过环境变量生效。",
                    "配置未即时同步");
            }

            IsDirty = false;
            _clearApiKeyRequested = false;
            _clearGithubTokenRequested = false;
            // 保存已全量推送后端：同步「已生效」快照，避免防抖计时器随后重复推送
            SyncLlmAppliedSnapshot();
            _savedApiKeyAtLoad = effectiveApiKey;
            _savedGithubTokenAtLoad = effectiveGithubToken;
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
            OnPropertyChanged(nameof(HasSavedGithubToken));
            var keyNote = !hasApiKeyInput && !_clearApiKeyRequested && !string.IsNullOrWhiteSpace(_savedApiKeyAtLoad)
                ? "；API Key 保留原值"
                : "";
            // 批次 2：重启类字段变更检测（后端连接/启动超时/后端命令 → 弹重启确认）
            var restartRequired = BackendUrl != _savedBackendUrlAtLoad
                || StartupTimeoutSec != _savedStartupTimeoutSecAtLoad
                || BackendCommand != _savedBackendCommandAtLoad;

            StatusMessage = pushFailed
                ? $"已保存（后端推送失败，重启后端后生效）{keyNote}"
                : $"已保存（模型/分块参数已实时生效；其余变更重启后端生效）{keyNote}";
            if (!pushFailed)
            {
                _notifications.Success("设置已保存");
            }
            DebugLog.Info($"设置保存成功: {settingsPath}", "Settings");

            // 顶部「当前生效配置」状态卡展示后端真实值：推送成功后重新拉取，
            // 否则卡片停留在打开设置页那一刻的旧值（表现为「改了默认模型却不刷新」）。
            // LoadBackendConfigAsync 内部 try/catch 静默，后端不可达不影响保存结果。
            // 刻意置于 StatusMessage 赋值之后：后端 ConfigError 可穿透覆盖「已保存」提示
            // （其分支无条件赋值），而 Notice 因 IsNullOrWhiteSpace 判断不会覆盖保存结果。
            if (!pushFailed)
            {
                await LoadBackendConfigAsync();
            }

            // 通知对话页同步默认提供商/模型（否则改默认模型保存后，对话页仍用旧默认/上次选择）
            RaiseProviderConfigChanged();

            // 批次 2：重启类字段变更后弹出「立即重启 / 稍后」确认对话框
            if (restartRequired)
            {
                var result = System.Windows.MessageBox.Show(
                    "检测到后端连接或启动参数已变更，需要重启后端才能生效。\n\n是否立即重启后端？",
                    "重启确认",
                    System.Windows.MessageBoxButton.YesNo,
                    System.Windows.MessageBoxImage.Question);
                if (result == System.Windows.MessageBoxResult.Yes)
                {
                    await RestartBackendAsync();
                }
                else
                {
                    ShowRestartBanner = true;
                    RestartBannerText = "💡 后端连接/启动参数已变更，需要重启后端才能生效。您可以稍后手动重启。";
                }
            }
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
        AutoCurateOnIngest = _appSettings.AutoCurateOnIngest;
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
        LlmTimeoutSec = _appSettings.LlmTimeoutSec > 0 ? _appSettings.LlmTimeoutSec : 300;
        WebSearchTimeoutSec = _appSettings.WebSearchTimeoutSec > 0 ? _appSettings.WebSearchTimeoutSec : 36;
        WebSearchSearxngUrl = _appSettings.WebSearchSearxngUrl;
        RagTopK = _appSettings.RagTopK;
        RagSystemPrompt = _appSettings.RagSystemPrompt;
        RagMaxHistoryTokens = _appSettings.RagMaxHistoryTokens;
        RerankEnabled = _appSettings.RerankEnabled;
        RerankModel = _appSettings.RerankModel;
        RerankRecall = _appSettings.RerankRecall;
        
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
        _githubToken = _appSettings.GithubToken;
        _clearGithubTokenRequested = false;
        // 批次 2：恢复重启类字段快照
        _savedBackendUrlAtLoad = _appSettings.BackendUrl;
        _savedStartupTimeoutSecAtLoad = _appSettings.StartupTimeoutSec;
        _savedBackendCommandAtLoad = _appSettings.BackendCommand;
        IsDirty = false;
        StatusMessage = "已恢复";
    }

    /// <summary>重置所有设置项为出厂默认值（new AppSettings() 的初始值）。
    /// 与 Revert 不同：Revert 恢复到上次保存的值，Reset 恢复到全新安装时的值。
    /// 不自动保存，用户需手动点「保存」生效。</summary>
    [RelayCommand]
    private void ResetToDefaults()
    {
        var defaults = new AppSettings();
        BackendUrl = defaults.BackendUrl;
        PollIntervalMs = defaults.PollIntervalMs;
        StartupTimeoutSec = defaults.StartupTimeoutSec;
        BackendCommand = defaults.BackendCommand;
        AutoStartBackend = defaults.AutoStartBackend;
        StopBackendOnExit = defaults.StopBackendOnExit;
        AutoCurateOnIngest = defaults.AutoCurateOnIngest;
        AutoIngestPath = defaults.AutoIngestPath;
        AutoIngestCollection = defaults.AutoIngestCollection;
        AutoIngestRecursive = defaults.AutoIngestRecursive;
        EmbedModel = defaults.EmbedModel;
        EmbedModelPath = defaults.EmbedModelPath;
        HfEndpoint = defaults.HfEndpoint;
        ChunkMaxTokens = defaults.ChunkMaxTokens;
        ChunkMinChars = defaults.ChunkMinChars;
        ChunkOverlapChars = defaults.ChunkOverlapChars;
        ChunkMaxChars = defaults.ChunkMaxChars;
        LlmProvider = defaults.LlmProvider;
        LlmApiKey = defaults.LlmApiKey;
        GithubToken = defaults.GithubToken;
        LlmBaseUrl = defaults.LlmBaseUrl;
        LlmModel = defaults.LlmModel;
        LlmTemperature = defaults.LlmTemperature;
        LlmMaxTokens = defaults.LlmMaxTokens;
        LlmTimeoutSec = defaults.LlmTimeoutSec;
        WebSearchTimeoutSec = defaults.WebSearchTimeoutSec;
        WebSearchSearxngUrl = defaults.WebSearchSearxngUrl;
        RagTopK = defaults.RagTopK;
        RagSystemPrompt = defaults.RagSystemPrompt;
        RagMaxHistoryTokens = defaults.RagMaxHistoryTokens;
        RagMode = defaults.RagMode;
        RerankEnabled = defaults.RerankEnabled;
        RerankModel = defaults.RerankModel;
        RerankRecall = defaults.RerankRecall;

        WatchPaths.Clear();
        WatchDebounceSeconds = defaults.WatchDebounceSeconds;

        if (SelectedTheme != ThemeMode.Light)
        {
            SelectedTheme = ThemeMode.Light;
        }

        _clearApiKeyRequested = false;
        _clearGithubTokenRequested = false;
        _savedApiKeyAtLoad = null;
        _savedBaseUrlAtLoad = null;
        _savedModelAtLoad = "";
        _savedRagSystemPromptAtLoad = null;
        _savedGithubTokenAtLoad = null;
        _backendApiKeyConfigured = false;
        // 批次 2：重置重启类字段快照
        _savedBackendUrlAtLoad = defaults.BackendUrl;
        _savedStartupTimeoutSecAtLoad = defaults.StartupTimeoutSec;
        _savedBackendCommandAtLoad = defaults.BackendCommand;

        IsDirty = true; // 需要手动保存才生效
        StatusMessage = "已重置为默认值（请点「保存」生效）";
        _notifications.Info("已将所有设置重置为出厂默认值，请检查后点击「保存」", "重置默认");
        DebugLog.Info("设置已重置为出厂默认值", "Settings");
    }

    /// <summary>显式清除已配置的 API Key（保存时本地置空并向后端推送清除）。
    /// key 输入框留空保存只会保留原值，不会清除——清除必须走此按钮。
    /// 批次 2：点击后先弹确认对话框（ShowApiKeyClearConfirm），确认后才执行。</summary>
    [RelayCommand]
    private void ClearApiKey()
    {
        // 有已配置的 key 时弹确认对话框；无 key 时直接清除
        if (HasSavedApiKey)
        {
            ShowApiKeyClearConfirm = true;
        }
        else
        {
            DoClearApiKey();
        }
    }

    /// <summary>确认清除 API Key（对话框确认后执行）。</summary>
    [RelayCommand]
    private void ConfirmClearApiKey()
    {
        ShowApiKeyClearConfirm = false;
        DoClearApiKey();
    }

    /// <summary>取消清除 API Key。</summary>
    [RelayCommand]
    private void CancelClearApiKey()
    {
        ShowApiKeyClearConfirm = false;
    }

    private void DoClearApiKey()
    {
        _clearApiKeyRequested = true;
        LlmApiKey = null;
        // 即使本地本就无 key（值未变化，setter 不会标记 dirty），清除请求本身
        // 也要进入保存流程：后端可能有 key（环境变量/手动配置），此时仍要推送
        // "" 显式清除。若没有这行，dirty=false 时保存按钮禁用，清除永远无法生效。
        IsDirty = true;
        SaveCommand.NotifyCanExecuteChanged();
    }

    /// <summary>显式清除已配置的 GitHub Token（保存时本地置空）。
    /// 输入框留空保存只会保留原值，不会清除——清除必须走此按钮。</summary>
    [RelayCommand]
    private void ClearGithubToken()
    {
        _clearGithubTokenRequested = true;
        GithubToken = null;
        // 同 ClearApiKey：值未变化时 setter 不标记 dirty，清除请求本身要进入保存流程
        IsDirty = true;
        SaveCommand.NotifyCanExecuteChanged();
    }

    // ===================== AI 提供商档案 =====================

    /// <summary>服务商下拉统一选项：内置预设（LlmPresetCatalog.All）+ 自定义服务商（SavedProfiles）合并。</summary>
    public System.Collections.ObjectModel.ObservableCollection<ProviderOption> ProviderOptions { get; } = new();

    private ProviderOption? _selectedProviderOption;

    /// <summary>当前选中的服务商选项（内置预设或自定义服务商）。</summary>
    public ProviderOption? SelectedProviderOption
    {
        get => _selectedProviderOption;
        set
        {
            if (SetProperty(ref _selectedProviderOption, value) && value is not null && !_isRebuildingProviderOptions)
            {
                if (value.IsBuiltIn && value.Preset is { } preset)
                {
                    // 内置预设：一键填入地址与推荐模型，名称自动默认
                    SelectedPreset = preset;
                }
                else if (value.IsCustom && value.Profile is { } profile)
                {
                    // 自定义服务商：回填表单供查看/编辑（不自动推送，「应用该服务商」按钮才保存+推送）
                    SelectedProfile = profile;
                    LoadProfileIntoForm(profile);
                }
            }
        }
    }

    /// <summary>把服务商配置回填到表单（Provider/地址/模型列表/默认模型/温度/token/Key/名称）。
    /// 仅回填不推送（「输入即生效」在回填期间抑制）；「应用该服务商」按钮才执行保存+推送。</summary>
    private void LoadProfileIntoForm(LlmProfile profile)
    {
        _isBackfillingLlmForm = true;
        try
        {
            LlmProvider = profile.Provider;
            LlmBaseUrl = profile.BaseUrl;
            // 模型下拉候选 = 该服务商全部模型（含上下文窗口元数据）；默认模型 = profile.Model（若不在候选则追加）
            LlmModels.Clear();
            var hasDefault = false;
            foreach (var m in profile.Models ?? new List<string>())
            {
                if (!string.IsNullOrWhiteSpace(m))
                {
                    int? ctx = profile.ModelContextWindows?.TryGetValue(m, out var cw) == true ? cw : null;
                    LlmModels.Add(new LlmModelItem(m.Trim(), ctx));
                    if (string.Equals(m.Trim(), profile.Model?.Trim(), StringComparison.OrdinalIgnoreCase))
                    {
                        hasDefault = true;
                    }
                }
            }
            if (!string.IsNullOrWhiteSpace(profile.Model) && !hasDefault)
            {
                int? ctx = profile.ModelContextWindows?.TryGetValue(profile.Model, out var cw) == true ? cw : null;
                LlmModels.Add(new LlmModelItem(profile.Model.Trim(), ctx));
            }
            LlmModel = profile.Model ?? "";
            if (profile.Temperature is not null)
            {
                LlmTemperature = profile.Temperature.Value;
            }
            if (profile.MaxTokens is not null)
            {
                LlmMaxTokens = profile.MaxTokens.Value;
            }
            // Replace the form value even when the profile has no key, so a prior
            // provider's secret cannot be carried into the newly selected profile.
            LlmApiKey = string.IsNullOrWhiteSpace(profile.ApiKey) ? null : profile.ApiKey;
            // 名称自动默认（用户可改）
            ProfileNameInput = profile.Name;
        }
        finally
        {
            _isBackfillingLlmForm = false;
        }
    }

    /// <summary>重建服务商下拉选项（内置预设 + 自定义服务商），并尽量保持当前选中项。
    /// 构造初始化与档案增删后调用；重建期间抑制「选中即应用」副作用。</summary>
    private void RebuildProviderOptions()
    {
        var currentId = SelectedProviderOption?.Id;
        _isRebuildingProviderOptions = true;
        try
        {
            ProviderOptions.Clear();
            foreach (var preset in LlmPresetCatalog.All)
            {
                ProviderOptions.Add(new ProviderOption($"preset:{preset.Id}", preset.DisplayName, preset.Description, preset, null));
            }
            foreach (var profile in SavedProfiles)
            {
                ProviderOptions.Add(new ProviderOption($"profile:{profile.Id}", profile.Name,
                    string.IsNullOrWhiteSpace(profile.Description) ? profile.Provider : profile.Description!,
                    null, profile));
            }
            // 恢复选中：优先保持原选中项，否则选中最后应用的档案，否则默认内置预设
            SelectedProviderOption = currentId is not null
                ? ProviderOptions.FirstOrDefault(o => o.Id == currentId)
                : (!string.IsNullOrWhiteSpace(_appSettings.ActiveProfileId)
                    ? ProviderOptions.FirstOrDefault(o => o.Id == $"profile:{_appSettings.ActiveProfileId}")
                    : null) ?? ProviderOptions.FirstOrDefault();
        }
        finally
        {
            _isRebuildingProviderOptions = false;
        }
    }

    /// <summary>已保存的 AI 提供商档案列表（可复用命名配置，ApiKey 内存明文/落盘加密）。</summary>
    public System.Collections.ObjectModel.ObservableCollection<LlmProfile> SavedProfiles { get; } = new();

    /// <summary>当前选中的档案。</summary>
    public LlmProfile? SelectedProfile
    {
        get => _selectedProfile;
        set
        {
            if (SetProperty(ref _selectedProfile, value))
            {
                OnPropertyChanged(nameof(HasSelectedProfile));
                OnPropertyChanged(nameof(SelectedProfileSummary));
                OnPropertyChanged(nameof(IsSelectedProviderDefault));
                OnPropertyChanged(nameof(IsSelectedProviderEnabled));
                // CanApplyProfile 依赖 HasSelectedProfile：选中/清空档案时一并通知，
                // 否则「应用该服务商」按钮的可用态不会随选中变化刷新
                OnPropertyChanged(nameof(CanApplyProfile));
            }
        }
    }

    public bool HasSelectedProfile => SelectedProfile is not null;

    public bool HasSavedProfiles => SavedProfiles.Count > 0;

    /// <summary>当前选中服务商是否为默认服务商（对话页「默认」项使用）。</summary>
    public bool IsSelectedProviderDefault =>
        SelectedProfile is { } p && p.Id == _appSettings.ActiveProfileId;

    /// <summary>当前选中服务商是否已启用（停用后不出现在对话页点选列表）。</summary>
    public bool IsSelectedProviderEnabled => SelectedProfile?.IsEnabled != false;

    /// <summary>把当前选中服务商设为默认并立即应用到全局配置、后端及各业务页。</summary>
    [RelayCommand]
    private async Task SetDefaultProviderAsync()
    {
        if (SelectedProfile is not { } profile)
        {
            StatusMessage = "请先在左侧选中一个自定义服务商，再设为默认";
            _notifications.Warning("请先在左侧选中一个自定义服务商", "设为默认");
            return;
        }
        await ApplyProfileAsync();
        OnPropertyChanged(nameof(IsSelectedProviderDefault));
        StatusMessage = $"已将「{profile.Name}」设为默认并应用到全部业务";
        _notifications.Success(StatusMessage, "默认服务商");
        DebugLog.Info($"设为默认并应用服务商: {profile.Name} ({profile.Id})", "Settings");
    }

    /// <summary>切换当前选中服务商的启用/停用状态（停用后不出现在对话页点选列表，配置保留）。</summary>
    [RelayCommand]
    private void ToggleProviderEnabled()
    {
        if (SelectedProfile is not { } profile)
        {
            StatusMessage = "请先在左侧选中一个自定义服务商";
            return;
        }
        profile.IsEnabled = !profile.IsEnabled;
        // LlmProfile is a persistence model rather than an observable item;
        // replace the collection slot so the profile list refreshes immediately.
        var profileIndex = SavedProfiles.IndexOf(profile);
        if (profileIndex >= 0)
        {
            SavedProfiles[profileIndex] = profile;
        }
        _appSettings.LlmProfiles = SavedProfiles.ToList();
        _appSettings.Save();
        OnPropertyChanged(nameof(IsSelectedProviderEnabled));
        StatusMessage = profile.IsEnabled
            ? $"已启用「{profile.Name}」（将出现在对话页模型点选列表）"
            : $"已停用「{profile.Name}」（不出现在对话页，配置保留）";
        _notifications.Info(StatusMessage, "服务商状态");
        DebugLog.Info($"切换服务商启用状态: {profile.Name} -> {profile.IsEnabled}", "Settings");
        RaiseProviderConfigChanged();
    }

    /// <summary>档案摘要（下拉 ToolTip/副标题）。</summary>
    public string SelectedProfileSummary => SelectedProfile is null ? ""
        : $"{SelectedProfile.Provider} · {SelectedProfile.Model ?? "默认模型"}"
          + (string.IsNullOrWhiteSpace(SelectedProfile.BaseUrl) ? "" : $" · {SelectedProfile.BaseUrl}");

    /// <summary>档案名称输入（新建档案用；留空 = 更新当前选中档案）。</summary>
    public string ProfileNameInput
    {
        get => _profileNameInput;
        set => SetProperty(ref _profileNameInput, value);
    }

    /// <summary>是否正在应用档案（应用+保存+推送进行中）。</summary>
    public bool IsApplyingProfile
    {
        get => _isApplyingProfile;
        set
        {
            if (SetProperty(ref _isApplyingProfile, value))
            {
                OnPropertyChanged(nameof(CanApplyProfile));
            }
        }
    }

    /// <summary>「应用该服务商」按钮是否可用：需已选中档案且当前不在应用中。
    /// 该按钮语义是把左侧选中档案「回填」到右侧表单，并非把右侧配置推送到后端——
    /// 旧版 ToolTip 把方向写反了（写成"应用右侧当前配置到后端"），导致用户点了
    /// 以为已保存、实际静默 return 什么都没发生。未选档案时禁用，
    /// 从源头避免「点了没反应」。</summary>
    public bool CanApplyProfile => HasSelectedProfile && !IsApplyingProfile;

    /// <summary>档案一键体检结果列表（每个档案一条：可用性/耗时/错误）。</summary>
    public System.Collections.ObjectModel.ObservableCollection<ProfileCheckResult> ProfileCheckResults { get; } = new();

    /// <summary>是否正在体检全部档案。</summary>
    public bool IsCheckingProfiles
    {
        get => _isCheckingProfiles;
        set => SetProperty(ref _isCheckingProfiles, value);
    }

    /// <summary>是否有体检结果可展示。</summary>
    public bool HasProfileCheckResults => ProfileCheckResults.Count > 0;

    /// <summary>删除档案的确认回调（默认弹 MessageBox；测试注入直接返回 true 以绕过对话框）。</summary>
    internal Func<LlmProfile, bool>? DeleteConfirm { get; set; }

    /// <summary>把当前表单（提供商/地址/模型/温度/token/Key）保存为 AI 档案。
    /// 名称取 ProfileNameInput，留空则更新当前选中档案；同名档案直接覆盖。</summary>
    [RelayCommand]
    private void SaveProfile()
    {
        var name = ProfileNameInput?.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            name = SelectedProfile?.Name?.Trim() ?? "";
        }
        if (string.IsNullOrWhiteSpace(name))
        {
            StatusMessage = "请先为档案输入名称，或在下拉中选择已有档案";
            _notifications.Warning("请先为档案输入一个名称，或先在下拉中选择已有档案", "保存档案");
            return;
        }
        if (string.IsNullOrWhiteSpace(LlmProvider) || LlmProvider == "none")
        {
            StatusMessage = "请先选择接口类型（提供商）再保存档案";
            _notifications.Warning("请先选择接口类型（提供商不能为 none）", "保存档案");
            return;
        }

        var existing = SavedProfiles.FirstOrDefault(p =>
            string.Equals(p.Name, name, StringComparison.OrdinalIgnoreCase));
        var target = existing ?? new LlmProfile { Name = name };
        target.Provider = LlmProvider;
        target.BaseUrl = string.IsNullOrWhiteSpace(LlmBaseUrl) ? null : LlmBaseUrl.Trim();
        // 默认模型：当前表单选中的模型
        target.Model = string.IsNullOrWhiteSpace(LlmModel) ? null : LlmModel.Trim();
        // 该服务商全部可用模型：当前表单模型下拉候选（含推荐/拉取/手输）
        target.Models = LlmModels
            .Where(m => !string.IsNullOrWhiteSpace(m.Name))
            .Select(m => m.Name.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();
        // 持久化上下文窗口（仅保存有值的模型）
        target.ModelContextWindows = LlmModels
            .Where(m => !string.IsNullOrWhiteSpace(m.Name) && m.ContextWindow is not null)
            .GroupBy(m => m.Name.Trim(), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(g => g.Key, g => g.First().ContextWindow!.Value, StringComparer.OrdinalIgnoreCase);
        target.Temperature = LlmTemperature;
        target.MaxTokens = LlmMaxTokens;
        target.KeyDecryptFailed = false;
        // key：输入非空才覆盖；留空保留档案原 key（与表单「留空保留」语义一致）
        if (!string.IsNullOrWhiteSpace(LlmApiKey))
        {
            target.ApiKey = LlmApiKey.Trim();
        }
        else if (existing is null)
        {
            target.ApiKey = null;
        }

        if (existing is null)
        {
            SavedProfiles.Add(target);
            StatusMessage = $"已添加服务商「{name}」（{target.Models.Count} 个模型）";
            _notifications.Success($"已添加自定义服务商「{name}」，可在服务商下拉中选用", "添加服务商");
        }
        else
        {
            StatusMessage = $"已更新服务商「{name}」（同名覆盖）";
            _notifications.Success($"已更新服务商「{name}」", "更新服务商");
        }
        SelectedProfile = target;
        ProfileNameInput = "";
        OnPropertyChanged(nameof(HasSavedProfiles));
        // 新服务商入列：同步重建合并服务商下拉（内置预设 + 自定义服务商）
        RebuildProviderOptions();

        // 落盘（AppSettings.Save 会对各档案 ApiKey 加密）
        _appSettings.LlmProfiles = SavedProfiles.ToList();
        _appSettings.Save();
        DebugLog.Info($"保存 AI 服务商: name={name} provider={target.Provider} model={target.Model ?? "-"} models={target.Models.Count}", "Settings");
        // 服务商已新增/更新：通知对话页立即重建模型候选
        RaiseProviderConfigChanged();
    }

    /// <summary>应用选中档案：回填表单 → 走完整保存流程（落盘 + 推送后端免重启生效）。</summary>
    [RelayCommand]
    private async Task ApplyProfileAsync()
    {
        if (SelectedProfile is not { } profile)
        {
            // 旧版此处静默 return：用户点了按钮毫无反馈，误以为配置已生效
            StatusMessage = "请先在左侧服务商列表中选中一个档案，再点「应用该服务商」";
            _notifications.Warning(
                "请先在左侧选中一个服务商档案。「应用该服务商」的用途是把档案回填到右侧表单，"
                + "不是把右侧配置保存到后端——推送到后端请用顶部「保存」，存为可复用档案请用「+ 添加为服务商」。",
                "应用档案");
            return;
        }
        if (IsApplyingProfile)
        {
            return;
        }
        IsApplyingProfile = true;
        try
        {
            if (profile.KeyDecryptFailed)
            {
                StatusMessage = $"⚠ 档案「{profile.Name}」的 API Key 无法解密，请重新输入后保存";
                _notifications.Warning(
                    $"档案「{profile.Name}」的 API Key 无法解密（可能更换过 Windows 用户或文件损坏），请重新输入。",
                    "应用档案");
            }

            // 回填表单（值变化自动置 IsDirty）
            LoadProfileIntoForm(profile);
            // Selecting a profile is form-only; applying a keyless profile must also
            // explicitly clear any previously configured global/backend key.
            _clearApiKeyRequested = string.IsNullOrWhiteSpace(profile.ApiKey);
            if (_clearApiKeyRequested)
            {
                IsDirty = true;
                SaveCommand.NotifyCanExecuteChanged();
            }
            // 记录激活档案（随 Save 落盘；仅高亮用，不强制改配置）
            _appSettings.ActiveProfileId = profile.Id;
            OnPropertyChanged(nameof(IsSelectedProviderDefault));
            // 走完整保存流程：本地落盘 + 推送后端（免重启生效）
            await SaveAsync();
            // key 解密失败的档案：应用成功但需重输 key——最终状态消息必须保留此提醒
            // （SaveAsync 内部会把 StatusMessage 覆盖为「已保存」，这里按优先级重写）
            StatusMessage = profile.KeyDecryptFailed
                ? $"⚠ 已应用档案「{profile.Name}」，但该档案的 API Key 无法解密，请重新输入后保存"
                : $"✅ 已应用档案「{profile.Name}」（{profile.Provider} / {profile.Model ?? "默认模型"}）";
            DebugLog.Info($"应用 AI 档案: name={profile.Name} provider={profile.Provider}", "Settings");
        }
        catch (Exception ex)
        {
            StatusMessage = $"❌ 应用档案失败: {ex.Message}";
            _notifications.Error($"应用档案失败：{ex.Message}");
            DebugLog.Error($"应用档案异常: {ex.Message}", "Settings", ex);
        }
        finally
        {
            IsApplyingProfile = false;
        }
    }

    /// <summary>删除选中档案（确认后不可恢复；可重新保存重建）。</summary>
    [RelayCommand]
    private void DeleteProfile()
    {
        if (SelectedProfile is not { } profile)
        {
            return;
        }
        var confirm = DeleteConfirm ?? DefaultDeleteConfirm;
        if (!confirm(profile))
        {
            return;
        }

        SavedProfiles.Remove(profile);
        if (_appSettings.ActiveProfileId == profile.Id)
        {
            _appSettings.ActiveProfileId = null;
        }
        _appSettings.LlmProfiles = SavedProfiles.ToList();
        _appSettings.Save();
        OnPropertyChanged(nameof(HasSavedProfiles));
        SelectedProfile = SavedProfiles.FirstOrDefault();
        // 服务商已删：同步重建合并服务商下拉
        RebuildProviderOptions();
        StatusMessage = $"已删除档案「{profile.Name}」（可重新保存恢复）";
        _notifications.Success($"已删除档案「{profile.Name}」", "删除档案");
        DebugLog.Info($"删除 AI 档案: {profile.Name}", "Settings");
        // 服务商已删除：通知对话页立即重建模型候选
        RaiseProviderConfigChanged();
    }

    private bool DefaultDeleteConfirm(LlmProfile profile)
    {
        var result = System.Windows.MessageBox.Show(
            $"确定删除 AI 提供商档案「{profile.Name}」吗？\n删除后不可恢复（可重新保存重建）。",
            "删除档案",
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Warning);
        return result == System.Windows.MessageBoxResult.Yes;
    }

    /// <summary>档案一键体检：对全部已存档案逐个调 /v1/llm/test，
    /// 收集可用性/耗时/错误到 ProfileCheckResults（对齐 Codex++ Provider Doctor）。
    /// key 无法解密的档案直接标记失败并提示，不请求后端。</summary>
    [RelayCommand]
    private async Task CheckAllProfilesAsync()
    {
        if (IsCheckingProfiles)
        {
            return;
        }
        if (SavedProfiles.Count == 0)
        {
            StatusMessage = "暂无档案可体检：先保存一个 AI 提供商档案";
            _notifications.Warning("暂无档案可体检，请先保存一个 AI 提供商档案", "档案体检");
            return;
        }

        IsCheckingProfiles = true;
        ProfileCheckResults.Clear();
        OnPropertyChanged(nameof(HasProfileCheckResults));
        StatusMessage = "正在体检全部 AI 档案…";
        DebugLog.Info($"开始档案一键体检: 共 {SavedProfiles.Count} 个", "Settings");

        var okCount = 0;
        try
        {
            foreach (var profile in SavedProfiles.ToList())
            {
                var result = new ProfileCheckResult(profile.Name, profile.Provider, false, 0, null);
                if (profile.KeyDecryptFailed)
                {
                    result = result with
                    {
                        Ok = false,
                        Error = "API Key 无法解密，请重新输入后保存",
                    };
                }
                else
                {
                    try
                    {
                        var test = await _apiService.LlmTestAsync(new LlmTestRequest
                        {
                            Provider = profile.Provider,
                            ApiKey = profile.ApiKey,
                            BaseUrl = profile.BaseUrl,
                            Model = profile.Model,
                            Timeout = 20,
                        });
                        result = result with
                        {
                            Ok = test.Ok,
                            ElapsedMs = test.ElapsedMs,
                            Error = test.Ok ? null : (test.Error ?? "未知错误"),
                        };
                        if (test.Ok)
                        {
                            okCount++;
                        }
                    }
                    catch (Exception ex)
                    {
                        DebugLog.Warn($"档案「{profile.Name}」体检异常: {ex.Message}", "Settings");
                        result = result with { Ok = false, Error = ex.Message };
                    }
                }
                ProfileCheckResults.Add(result);
            }

            OnPropertyChanged(nameof(HasProfileCheckResults));
            StatusMessage = okCount == SavedProfiles.Count
                ? $"✅ 全部 {SavedProfiles.Count} 个档案体检通过"
                : $"档案体检完成：{okCount}/{SavedProfiles.Count} 可用";
            DebugLog.Info($"档案体检完成: {okCount}/{SavedProfiles.Count} 可用", "Settings");
            if (okCount < SavedProfiles.Count)
            {
                _notifications.Warning(
                    $"{SavedProfiles.Count - okCount} 个档案不可用，详情见下方列表", "档案体检");
            }
            else if (SavedProfiles.Count > 0)
            {
                _notifications.Success($"全部 {SavedProfiles.Count} 个 AI 档案可用", "档案体检");
            }
        }
        finally
        {
            IsCheckingProfiles = false;
        }
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
        StartProgress("测试连接", "检测后端与 LLM…");
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
        StopProgress();
        }
    }

    /// <summary>拉取当前提供商的可用模型列表（POST /v1/llm/models）。
    /// 用 UI 当前输入值（无需先保存）：Ollama 列本地已装模型，云端调各家 /models 接口；
    /// key/base_url 留空时后端沿用当前运行时配置。失败不打断（仍可手输模型名）。</summary>
    [RelayCommand]
    private async Task RefreshLlmModelsAsync()
    {
        if (string.IsNullOrWhiteSpace(LlmProvider) || LlmProvider == "none")
        {
            StatusMessage = "❌ 请先选择 LLM 提供商，再获取模型列表";
            return;
        }

        var requestVersion = ++_llmModelsRequestVersion;
        var requestedProvider = LlmProvider.Trim();
        var requestedApiKey = string.IsNullOrWhiteSpace(LlmApiKey) ? null : LlmApiKey.Trim();
        var requestedBaseUrl = string.IsNullOrWhiteSpace(LlmBaseUrl) ? null : LlmBaseUrl.Trim();
        IsFetchingModels = true;
        StatusMessage = "获取模型列表中…";
        DebugLog.Info($"开始获取模型列表: provider={requestedProvider}", "Settings");

        try
        {
            var result = await _apiService.LlmModelsAsync(new LlmModelsRequest
            {
                Provider = requestedProvider,
                ApiKey = requestedApiKey,
                BaseUrl = requestedBaseUrl,
                Timeout = 10,
            });

            if (requestVersion != _llmModelsRequestVersion)
            {
                DebugLog.Info($"忽略过期模型列表响应: provider={requestedProvider}", "Settings");
                return;
            }

            var inputsStillMatch = string.Equals(requestedProvider, LlmProvider?.Trim(), StringComparison.OrdinalIgnoreCase)
                && string.Equals(requestedApiKey, string.IsNullOrWhiteSpace(LlmApiKey) ? null : LlmApiKey.Trim(), StringComparison.Ordinal)
                && string.Equals(requestedBaseUrl, string.IsNullOrWhiteSpace(LlmBaseUrl) ? null : LlmBaseUrl.Trim(), StringComparison.OrdinalIgnoreCase);
            if (!inputsStillMatch || (result.Ok && !string.Equals(requestedProvider, result.Provider, StringComparison.OrdinalIgnoreCase)))
            {
                StatusMessage = "配置已变更，旧模型列表结果已丢弃，请重新获取";
                DebugLog.Info($"忽略过期模型列表响应: provider={requestedProvider}", "Settings");
                return;
            }

            if (result.Ok)
            {
                LlmModels.Clear();
                foreach (var m in result.Models)
                {
                    int? ctx = null;
                    int? maxOut = null;
                    bool isReasoning = false;
                    string? summary = null;
                    if (result.ModelMeta.TryGetValue(m, out var meta))
                    {
                        ctx = meta.ContextWindow;
                        maxOut = meta.MaxOutputTokens;
                        isReasoning = meta.IsReasoningModel;
                        summary = meta.SummaryText;
                    }
                    LlmModels.Add(new LlmModelItem(m, ctx, maxOut, isReasoning, summary));
                }
                // 自动选中第一个模型作为默认模型（全量导入后无需手动点选）
                LlmModel = LlmModels.FirstOrDefault()?.Name ?? LlmModel;
                SelectedModelCandidate = LlmModels.FirstOrDefault();
                _appSettings.LlmAvailableModels = LlmModels
                    .Select(item => item.Name.Trim())
                    .Where(name => name.Length > 0)
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .ToList();
                _appSettings.LlmProvider = LlmProvider;
                _appSettings.LlmBaseUrl = LlmBaseUrl;
                _appSettings.LlmModel = LlmModel;
                _appSettings.Save();
                StatusMessage = $"✅ 获取到 {result.Models.Count} 个模型（{result.Provider}），已自动导入全部模型到该服务商";
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
            if (requestVersion == _llmModelsRequestVersion)
            {
                IsFetchingModels = false;
            }
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

        StatusMessage = "正在平滑重启后端服务...";
        _notifications.Info("正在平滑重启后端服务...", "重启中");
        try
        {
            var ok = await _backendProcess.RestartAsync();
            if (ok)
            {
                ShowRestartBanner = false;
                StatusMessage = "✅ 后端服务已成功重启并对齐最新端口与配置";
                _notifications.Success("后端服务已平滑重启，最新配置已全面生效！", "重启成功");
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

/// <summary>AI 提供商档案一键体检结果项（对齐 Codex++ Provider Doctor）。
/// Ok=true 表示该档案经 /v1/llm/test 连通可用；Error 为失败原因（已分类中文提示）。</summary>
public sealed record ProfileCheckResult(
    string Name,
    string Provider,
    bool Ok,
    int ElapsedMs,
    string? Error)
{
    public string StatusText => Ok ? $"✅ 可用（{ElapsedMs}ms）" : $"❌ 不可用";
    public string? ErrorDetail => Ok ? null : (Error ?? "未知错误");
}

/// <summary>服务商下拉统一选项：内置预设（Preset 非空）或自定义服务商（Profile 非空）。
/// 设置页与对话页共用同一套选项模型，选中后按来源分派（内置 → ApplyPreset；自定义 → 应用服务商）。</summary>
public sealed record ProviderOption(
    string Id,
    string DisplayName,
    string Description,
    LlmPreset? Preset,
    LlmProfile? Profile)
{
    public bool IsBuiltIn => Preset is not null;

    /// <summary>是否自定义服务商（用户添加）。</summary>
    public bool IsCustom => Profile is not null;

    public override string ToString() => DisplayName;
}


/// <summary>自定义嵌入模型选项。</summary>
public sealed record CustomEmbedOption(string Id, string Label, string Note)
{
    public string Display => $"{Label} — {Note}";
}

/// <summary>自定义对话模型选项。</summary>
public sealed record CustomChatOption(string Id, string Label, string Note)
{
    public string Display => $"{Label} — {Note}";
}
