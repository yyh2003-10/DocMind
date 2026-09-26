using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;
using Markdig;
using Markdig.Wpf;

namespace DocMind.ViewModels;

/// <summary>可勾选的知识库集合项（复选框用）。</summary>
public sealed class CollectionItem : System.ComponentModel.INotifyPropertyChanged
{
    private string _name = string.Empty;
    private bool _isSelected;

    public string Name
    {
        get => _name;
        set
        {
            if (_name != value)
            {
                _name = value;
                OnPropertyChanged(nameof(Name));
            }
        }
    }

    public bool IsSelected
    {
        get => _isSelected;
        set
        {
            if (_isSelected != value)
            {
                _isSelected = value;
                OnPropertyChanged(nameof(IsSelected));
            }
        }
    }

    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;

    private void OnPropertyChanged(string propertyName)
        => PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(propertyName));
}

/// <summary>历史会话列表项（侧边栏与历史列表显示用）。</summary>
public sealed class ChatSessionItem
{
    public string ChatId { get; init; } = string.Empty;

    /// <summary>会话标题（首条用户问题前 50 字）。</summary>
    public string Title { get; init; } = string.Empty;

    public int MessageCount { get; init; }

    /// <summary>最后更新时间（ISO，来自后端）。</summary>
    public string UpdatedAt { get; init; } = string.Empty;

    /// <summary>简短知识库前缀标签（类似参考设计的 @知识库 标识）。</summary>
    public string Tag => "@默认知识库";

    /// <summary>会话主标题显示文本。</summary>
    public string DisplayTitle => string.IsNullOrWhiteSpace(Title) ? "新对话" : Title;

    /// <summary>下拉显示文本。</summary>
    public string Display
    {
        get
        {
            var title = string.IsNullOrWhiteSpace(Title) ? ChatId : Title;
            return MessageCount > 0 ? $"{title}（{MessageCount} 条）" : title;
        }
    }
}

public partial class ChatViewModel : ViewModelBase
{

    /// <summary>后端不可达时通知 Main 刷新全局离线横幅（FC-03/08）。</summary>
    public event Action? BackendUnreachable;

    private readonly IDoc2kbApiService _apiService;
    private readonly NotificationService? _notifications;

    /// <summary>用户点击引用来源时的请求事件。MainViewModel 订阅后导航到搜索页并传入源文件名。</summary>
    public event Action<SourceRef>? SourceSearchRequested;

    /// <summary>用户点击「前往配置大模型」请求事件。MainViewModel 订阅后跳转到设置页。</summary>
    public event Action? NavigateToSettingsRequested;

    /// <summary>用户点击「导入资料」请求事件。MainViewModel 订阅后跳转到导入页。</summary>
    public event Action? NavigateToImportRequested;

    /// <summary>用户点击「去重建索引」请求事件。MainViewModel 订阅后跳转到文档页。</summary>
    public event Action? NavigateToDocumentsRequested;

    /// <summary>导航前往设置页。</summary>
    [RelayCommand]
    private void NavigateToSettings() => NavigateToSettingsRequested?.Invoke();

    /// <summary>导航前往导入页。</summary>
    [RelayCommand]
    private void NavigateToImport() => NavigateToImportRequested?.Invoke();

    /// <summary>导航前往文档页（重建索引入口）。</summary>
    [RelayCommand]
    private void NavigateToDocuments() => NavigateToDocumentsRequested?.Invoke();
    // ===================== 知识库健康：换嵌入后未重建 → 对话页持久横幅（P0.1） =====================

    private bool _libraryNeedsReindex;
    private string _libraryStatusSummary = "";

    /// <summary>知识库是否需要重建索引（换嵌入模型/维度不一致时 true）。</summary>
    public bool LibraryNeedsReindex
    {
        get => _libraryNeedsReindex;
        private set
        {
            if (SetProperty(ref _libraryNeedsReindex, value))
                OnPropertyChanged(nameof(ShowLibraryReindexBanner));
        }
    }

    /// <summary>库状态摘要（横幅副文案）。</summary>
    public string LibraryStatusSummary
    {
        get => _libraryStatusSummary;
        private set => SetProperty(ref _libraryStatusSummary, value);
    }

    /// <summary>是否显示重建索引横幅。</summary>
    public bool ShowLibraryReindexBanner => LibraryNeedsReindex;
    // ===================== L2 权限确认卡片 =====================

    private string? _pendingPermissionRequestId;
    private string _pendingPermissionToolId = "";
    private bool _hasPendingPermission;

    /// <summary>是否有待确认的 Agent 写入权限请求。</summary>
    public bool HasPendingPermission
    {
        get => _hasPendingPermission;
        private set => SetProperty(ref _hasPendingPermission, value);
    }

    /// <summary>待确认权限的请求 ID。</summary>
    public string? PendingPermissionRequestId
    {
        get => _pendingPermissionRequestId;
        private set => SetProperty(ref _pendingPermissionRequestId, value);
    }

    /// <summary>待确认权限的工具名。</summary>
    public string PendingPermissionToolId
    {
        get => _pendingPermissionToolId;
        private set => SetProperty(ref _pendingPermissionToolId, value);
    }

    /// <summary>允许本次写入（并授权本会话后续写入）。</summary>
    [RelayCommand]
    private async Task AllowPermissionAsync()
    {
        var id = PendingPermissionRequestId;
        if (string.IsNullOrWhiteSpace(id)) return;
        try
        {
            await _apiService.ResolveAgentPermissionAsync(id, allow: true);
            StatusMessage = "已授权工作区写入";
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"权限放行失败: {ex.Message}", "Chat");
        }
        finally
        {
            HasPendingPermission = false;
            PendingPermissionRequestId = null;
        }
    }

    /// <summary>拒绝本次写入。</summary>
    [RelayCommand]
    private async Task DenyPermissionAsync()
    {
        var id = PendingPermissionRequestId;
        if (string.IsNullOrWhiteSpace(id)) return;
        try
        {
            await _apiService.ResolveAgentPermissionAsync(id, allow: false);
            StatusMessage = "已拒绝工作区写入";
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"权限拒绝失败: {ex.Message}", "Chat");
        }
        finally
        {
            HasPendingPermission = false;
            PendingPermissionRequestId = null;
        }
    }

    /// <summary>刷新知识库状态（启动/切到对话页/后端恢复时调用）。</summary>
    [RelayCommand]
    public async Task RefreshLibraryStatusAsync()
    {
        try
        {
            var status = await _apiService.GetLibraryStatusAsync();
            LibraryNeedsReindex = status.NeedsReindex;
            LibraryStatusSummary = string.IsNullOrWhiteSpace(status.Summary)
                ? (status.NeedsReindex ? "嵌入模型或维度与索引不一致，请重建索引后再提问。" : "")
                : status.Summary;
        }
        catch
        {
            // 后端不可达时不打扰对话页；下次恢复在线再刷
        }
    }

    private string _inputText = string.Empty;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private string? _chatId;
    private bool _isLoadingSessions;

    /// <summary>模型下拉首项伪值：表示「用设置页配置的默认模型」。</summary>
    public const string DefaultModelLabel = "默认（设置页模型）";

    private string _configuredProvider = "none";
    private string _configuredModel = "";

    /// <summary>「默认提供商（设置页全局配置）」分组的候选模型：本地 AppSettings 种子 + 🔄 拉取结果。
    /// 点选该组模型只随请求带 model 参数覆盖，不携带 ProviderConfig、不改全局配置。</summary>
    private readonly List<string> _defaultProviderModels = new();

    /// <summary>默认提供商（设置页全局配置）是否已配置可用 Key（后端 llm_api_key_configured 或本地 LlmApiKey 非空）。
    /// 未配置 Key 时不显示默认项与默认分组——只显示已配置好的供应商。</summary>
    private bool _defaultKeyConfigured;

    /// <summary>各启用服务商档案（有 Key）实时拉取的模型列表缓存（按 Profile Id）。
    /// 仅本会话显示用，不落盘；RebuildModelChoices 时并入对应档案的 p.Models 一起列出。</summary>
    private readonly Dictionary<string, List<string>> _profileLiveModels = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>联网搜索模式候选：关闭 / 普通搜索 / 深度搜索。</summary>
    public ObservableCollection<WebSearchModeChoice> WebSearchModes { get; } =
        new(WebSearchModeChoice.All);

    /// <summary>回答模式候选：自动 / 知识库 RAG / Agent 工具。</summary>
    public ObservableCollection<ChatModeChoice> ChatModes { get; } =
        new(ChatModeChoice.All);

    private ChatModeChoice _selectedChatMode = ChatModeChoice.Auto;

    /// <summary>当前回答模式（持久化 LastChatMode）。</summary>
    public ChatModeChoice SelectedChatMode
    {
        get => _selectedChatMode;
        set
        {
            var next = value ?? ChatModeChoice.Auto;
            if (SetProperty(ref _selectedChatMode, next))
            {
                _appSettings.LastChatMode = next.Key;
                try { _appSettings.Save(); } catch { /* 落盘失败不阻断 */ }
                OnPropertyChanged(nameof(ChatModeHint));
            }
        }
    }

    /// <summary>输入框旁模式提示（总闸关闭时提醒 Agent 会回落）。</summary>
    public string ChatModeHint =>
        SelectedChatMode.Key == "agent" && !_appSettings.AgentModeEnabled
            ? "Agent 总闸未开，本轮将回落 RAG"
            : SelectedChatMode.Label;

    private bool _isHtmlAnswerMode;

    /// <summary>HTML 体验模式：开启后本轮回答按整页 HTML 沙箱气泡渲染（持久化 LastAnswerFormat）。
    /// 注意 HTML 气泡比纯 Markdown 多 40%–150% token，UI 开关旁须有提示。</summary>
    public bool IsHtmlAnswerMode
    {
        get => _isHtmlAnswerMode;
        set
        {
            if (SetProperty(ref _isHtmlAnswerMode, value))
            {
                _appSettings.LastAnswerFormat = value ? "html" : "markdown";
                try { _appSettings.Save(); } catch { /* 落盘失败不阻断 */ }
            }
        }
    }

    /// <summary>HTML 气泡是否允许引用公网 CDN 资源（透传设置页 HtmlAllowCdn，气泡 CSP 消费）。</summary>
    public bool HtmlAllowCdn => _appSettings.HtmlAllowCdn;

    /// <summary>是否开启实时联网搜索（true = 普通或深度；兼容旧绑定/测试）。</summary>
    public bool IsWebSearchEnabled
    {
        get => SelectedWebSearchMode.Key is "normal" or "deep";
        set
        {
            if (value)
            {
                if (SelectedWebSearchMode.Key == "off")
                {
                    SelectedWebSearchMode = WebSearchModeChoice.Normal;
                }
            }
            else if (SelectedWebSearchMode.Key != "off")
            {
                SelectedWebSearchMode = WebSearchModeChoice.Off;
            }
        }
    }

    /// <summary>当前联网搜索模式（持久化：重启后保持上次选择）。</summary>
    public WebSearchModeChoice SelectedWebSearchMode
    {
        get => _selectedWebSearchMode;
        set
        {
            var next = value ?? WebSearchModeChoice.Off;
            if (SetProperty(ref _selectedWebSearchMode, next))
            {
                _appSettings.SetWebSearchMode(next.Key);
                try
                {
                    _appSettings.Save();
                }
                catch
                {
                    // 落盘失败不阻断对话
                }
                OnPropertyChanged(nameof(IsWebSearchEnabled));
            }
        }
    }

    public const string DefaultProfileLabel = "默认（设置页配置）";

    /// <summary>模型选择器候选（首项为「设置页默认」伪值，其后为各启用服务商的模型，显示「模型名（服务商名）」）。</summary>
    public ObservableCollection<ModelChoice> ModelChoices { get; } = new();

    private readonly AppSettings _appSettings;
    private ModelChoice? _selectedModelChoice;
    private WebSearchModeChoice _selectedWebSearchMode = WebSearchModeChoice.Off;

    /// <summary>构造期间抑制选择持久化（避免初始 RebuildModelChoices 覆盖上次落盘的模型选择）。</summary>
    private bool _isInitializingModelChoice;

    /// <summary>当前选中模型项；选中自定义服务商的模型 → 记录该服务商（发送时随请求携带 ProviderConfig）；默认项 → 用后端全局配置。</summary>
    public ModelChoice? SelectedModelChoice
    {
        get => _selectedModelChoice;
        set
        {
            if (SetProperty(ref _selectedModelChoice, value) && value is not null)
            {
                // 点选即持久化：新建会话/重启后还原上次选择的模型（与 EnableWebSearch 同机制）
                if (!_isInitializingModelChoice)
                {
                    PersistModelChoice(value);
                }
                // 单一事实源：Effective*/SelectedModel 均由此派生，避免双轨状态不同步
                OnPropertyChanged(nameof(SelectedModel));
                OnPropertyChanged(nameof(EffectiveProvider));
                OnPropertyChanged(nameof(EffectiveModel));
                OnPropertyChanged(nameof(EffectiveModelSummary));
                OnPropertyChanged(nameof(IsLlmConfigured));
                OnPropertyChanged(nameof(EmptyGuideText));
                StatusMessage = value.IsDefault ? "就绪" : $"模型: {value.DisplayName}（下条消息生效）";
            }
        }
    }

    /// <summary>把对话页模型选择落盘（AppSettings.LastChatModel/LastChatProfileId），重启/新建对话后还原。
    /// 默认项 → 两项皆空；默认提供商分组（裸模型名）→ 仅模型名；某服务商模型 → 模型名 + 档案 Id。</summary>
    private void PersistModelChoice(ModelChoice choice)
    {
        _appSettings.LastChatProfileId = choice.Provider?.Id;
        _appSettings.LastChatModel = choice.IsDefault ? null : choice.Model;
        try
        {
            _appSettings.Save();
        }
        catch
        {
            // 落盘失败不阻断对话
        }
    }

    // ── 跨会话记忆（Phase 1） ──
    private readonly UserMemoryService? _userMemory;
    private readonly SessionSearchService? _sessionSearch;
    // ── 成本追踪（Phase 3） ──
    private readonly CostTracker? _costTracker;
    // ── 反馈循环（Phase 4） ──
    private readonly FeedbackService? _feedback;

    public ChatViewModel(IDoc2kbApiService apiService, NotificationService? notifications = null, AppSettings? appSettings = null,
        UserMemoryService? userMemory = null, SessionSearchService? sessionSearch = null,
        CostTracker? costTracker = null, FeedbackService? feedback = null)
    {
        _apiService = apiService;
        _notifications = notifications;
        _appSettings = appSettings ?? new AppSettings();
        _userMemory = userMemory;
        _sessionSearch = sessionSearch;
        _costTracker = costTracker;
        _feedback = feedback;
        Title = "对话";
        _selectedPersona = AvailablePersonas[0];

        // 合并内置 + 用户自定义的角色与主题
        MergeCustomItems();

        Collections = new ObservableCollection<CollectionItem>();
        Collections.CollectionChanged += (_, e) =>
        {
            if (e.NewItems is not null)
            {
                foreach (CollectionItem item in e.NewItems)
                {
                    item.PropertyChanged += OnCollectionItemChanged;
                }
            }
            if (e.OldItems is not null)
            {
                foreach (CollectionItem item in e.OldItems)
                {
                    item.PropertyChanged -= OnCollectionItemChanged;
                }
            }
            OnPropertyChanged(nameof(HasSelectedCollection));
            OnPropertyChanged(nameof(KnowledgeAnchorText));
            SendCommand.NotifyCanExecuteChanged();
        };
        LoadCollectionsCommand = new AsyncRelayCommand(LoadCollectionsAsync);
        AddCollectionCommand = new AsyncRelayCommand<string?>(AddCollectionAsync);

        Sessions = new ObservableCollection<ChatSessionItem>();
        Sessions.CollectionChanged += (_, _) => OnPropertyChanged(nameof(FilteredSessions));

        // 默认提供商（设置页全局配置）候选模型：先用本地 AppSettings 同步种子，后端拉回后再补充。
        // provider 无条件同步（IsLlmConfigured/EffectiveProvider 的判断依据）；
        // 模型名为空时仅跳过默认分组种子（无法预设具体模型）。
        _configuredProvider = string.IsNullOrWhiteSpace(_appSettings.LlmProvider) ? "none" : _appSettings.LlmProvider;
        // 默认提供商是否"已配置好"：本地 LlmApiKey 非空视为已配置；后端拉回后用 llm_api_key_configured 覆盖
        _defaultKeyConfigured = IsKeyOptionalProvider(_appSettings.LlmProvider)
            || !string.IsNullOrWhiteSpace(_appSettings.LlmApiKey);
        if (!string.IsNullOrWhiteSpace(_appSettings.LlmModel))
        {
            _configuredModel = _appSettings.LlmModel;
            _defaultProviderModels.Add(_appSettings.LlmModel.Trim());
        }
        // 设置页「获取模型列表」持久化下来的可用模型（重启后仍可直接点选，无需再到对话页点刷新）
        SeedAvailableModelsFromSettings();
        // 已持久化的「默认提供商分组」选择（无档案）：补入候选，重启后即使不在默认模型种子也能还原
        if (string.IsNullOrWhiteSpace(_appSettings.LastChatProfileId)
            && !string.IsNullOrWhiteSpace(_appSettings.LastChatModel))
        {
            _defaultProviderModels.Add(_appSettings.LastChatModel.Trim());
        }

        // 还原回答模式（LastChatMode → DefaultChatMode）
        _selectedChatMode = ChatModeChoice.FromKey(_appSettings.ResolveChatMode());
        OnPropertyChanged(nameof(SelectedChatMode));
        OnPropertyChanged(nameof(ChatModeHint));

        // 还原 HTML 体验模式开关（LastAnswerFormat；异常值按 markdown）
        _isHtmlAnswerMode = string.Equals(
            (_appSettings.LastAnswerFormat ?? "markdown").Trim().ToLowerInvariant(), "html",
            StringComparison.Ordinal);

        // 模型选择器：首项「默认模型」伪值 + 默认提供商分组 + 各启用服务商的全部模型
        // Key 已在 App.LoadSettings 解密为明文；停用的服务商不出现在点选列表
        // 还原上次对话页选择的模型（持久化字段）；无记录时默认选中首项「默认模型」
        // （不再按 ActiveProfileId 高亮档案模型——用户明确要求默认选中默认项）
        _isInitializingModelChoice = true;
        try
        {
            RebuildModelChoices();
            var lastProfileId = _appSettings.LastChatProfileId;
            var lastModel = _appSettings.LastChatModel;
            SelectedModelChoice = ModelChoices.FirstOrDefault(c =>
                    c.Provider?.Id == lastProfileId && c.Model == lastModel)
                ?? ModelChoices.FirstOrDefault(c => c.Provider is null && c.Model == lastModel)
                ?? ModelChoices.FirstOrDefault(c =>
                    c.Provider is { } twin && IsSameEndpointAsDefault(twin) && c.Model == lastModel)
                ?? ModelChoices.FirstOrDefault();
        }
        finally
        {
            _isInitializingModelChoice = false;
        }

        // 恢复上次选择的联网搜索模式（旧配置仅有 EnableWebSearch 时自动迁移）
        _selectedWebSearchMode = WebSearchModeChoice.FromKey(_appSettings.ResolveWebSearchMode());

        // 恢复上次拖动的右侧抽屉宽度（直接读字段、不走 setter，避免构造期触发落盘）
        // 配置文件被手改成越界值时钳制回合法区间；默认 380。
        _sourceDrawerWidth = System.Math.Clamp(
            _appSettings.ChatDrawerWidth > 0 ? _appSettings.ChatDrawerWidth : DefaultSourceDrawerWidth,
            MinSourceDrawerWidth, MaxSourceDrawerWidth);

        Messages.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(HasMessages));
            OnPropertyChanged(nameof(ShowEmptyGuide));
            OnPropertyChanged(nameof(EmptyGuideText));
            OnPropertyChanged(nameof(MessagesCountText));
            OnPropertyChanged(nameof(HasHtmlMessage));
        OnPropertyChanged(nameof(HtmlDisplaySource));
        OnPropertyChanged(nameof(IsHtmlStreaming));
            UpdateMessageFlags();
        };

        // 构造时拉取已有知识库集合 + 历史会话 + 后端当前模型（模型下拉种子）
        _ = LoadCollectionsAsync();
        _ = LoadSessionsAsync();
        _ = SeedModelFromConfigAsync();

        // 服务商配置变更订阅已上移至 MainViewModel（静态事件由页面容器统一管理），
        // 设置页新增/更新/删除/启停服务商后 Main 会调用 ApplyProviderConfigChanged
    }

    /// <summary>设置页服务商配置变更回调（由 MainViewModel 订阅静态事件转调）：同步默认提供商配置后重建对话页模型候选（首项默认模型名 + 各启用服务商模型）。</summary>
    public void ApplyProviderConfigChanged()
    {
        // 设置页保存后同步默认提供商（provider/model，含清空），让首项「默认 · xx」显示最新默认模型。
        // 默认 provider/model 变更时回落到首项「默认」并清除「记住的上次选择」——
        // 否则设置页改了默认模型并保存后，对话页仍恢复旧模型，表现为「默认模型改不了/不生效」。
        var newProvider = string.IsNullOrWhiteSpace(_appSettings.LlmProvider) ? "none" : _appSettings.LlmProvider;
        var newModel = _appSettings.LlmModel ?? "";
        var defaultChanged = !string.Equals(_configuredProvider, newProvider, StringComparison.OrdinalIgnoreCase)
            || !string.Equals(_configuredModel, newModel, StringComparison.OrdinalIgnoreCase);
        _configuredProvider = newProvider;
        _configuredModel = newModel;
        // 本地有 Key 即视为已配置；本地为空时保留后端权威态（Key 可能由后端 config.toml
        // 或环境变量提供）。不可用本地空值覆盖为 false——否则整个默认分组与
        // 「默认 · xx」首项都会消失，表现为保存后对话页模型下拉变空。
        if (IsKeyOptionalProvider(newProvider) || !string.IsNullOrWhiteSpace(_appSettings.LlmApiKey))
        {
            _defaultKeyConfigured = true;
        }
        // 同步设置页保存下来的可用模型列表（设置页点过「获取模型列表」后立即可点选）
        SeedAvailableModelsFromSettings();
        OnPropertyChanged(nameof(IsLlmConfigured));
        OnPropertyChanged(nameof(EmptyGuideText));
        RebuildModelChoices();
        if (defaultChanged)
        {
            SelectedModelChoice = ModelChoices.FirstOrDefault(c => c.IsDefault) ?? ModelChoices.FirstOrDefault();
        }
    }

    /// <summary>把设置页持久化下来的可用模型列表（AppSettings.LlmAvailableModels）
    /// 并入默认提供商分组候选 _defaultProviderModels（去重、忽略空值）。
    /// 可重复调用：构造期播种与设置页保存后同步共用同一段逻辑。</summary>
    private void SeedAvailableModelsFromSettings()
    {
        var saved = _appSettings.LlmAvailableModels;
        if (saved is null || saved.Count == 0)
        {
            return;
        }
        foreach (var raw in saved)
        {
            var name = raw?.Trim();
            if (string.IsNullOrWhiteSpace(name))
            {
                continue;
            }
            // 去重交由 RebuildModelChoices 的 Distinct 兜底，这里只需避免重复入列
            if (!_defaultProviderModels.Any(m => string.Equals(m?.Trim(), name, StringComparison.OrdinalIgnoreCase)))
            {
                _defaultProviderModels.Add(name);
            }
        }
    }

    /// <summary>重建模型选择器候选：只显示「已配置好」的供应商——
    /// 默认提供商（设置页全局）仅在已配 Key 时显示首项「默认 · xx」+ 默认分组；
    /// 各启用且有 Key 的服务商档案列出其模型（保存的 Models + 本会话实时拉取的 _profileLiveModels）。
    /// 未配置任何 Key 时不显示任何项（空态引导由 EmptyGuideText 接管）。</summary>
    public void RebuildModelChoices()
    {
        var currentId = SelectedModelChoice?.Provider?.Id;
        var currentModel = SelectedModelChoice?.Model;
        ModelChoices.Clear();
        // 默认提供商分组：仅在已配 Key 时显示（点选它发送时用后端全局 provider/key/地址）
        if (_defaultKeyConfigured)
        {
            // 首项：真实默认模型名（设置页 LlmModel / 后端配置），无配置时回退到通用标签
            var defaultModelName = string.IsNullOrWhiteSpace(_configuredModel) ? null : _configuredModel.Trim();
            ModelChoices.Add(new ModelChoice(
                defaultModelName is null ? DefaultProfileLabel : $"默认 · {defaultModelName}",
                null,
                null));
            // 默认提供商分组：显示裸模型名，发送时只带 model 参数覆盖（用设置页的 provider/key/地址）；
            // 与默认端点相同的启用档案在下方已带「（服务商名）」列出同名模型，这里跳过，避免同一端点同模型出现两条
            var twinModels = CollectSameEndpointProfileModels();
            foreach (var m in _defaultProviderModels
                .Where(m => !string.IsNullOrWhiteSpace(m))
                .Select(m => m.Trim())
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .Where(m => !twinModels.Contains(m)))
            {
                ModelChoices.Add(new ModelChoice(m, null, m));
            }
        }
        if (_appSettings.LlmProfiles is { Count: > 0 })
        {
            // 只显示启用且已配 Key 的服务商档案（"已配置好"才算可选）
            foreach (var p in _appSettings.LlmProfiles.Where(p => p is not null && p.IsEnabled
                && (string.Equals(p.Provider, "ollama", StringComparison.OrdinalIgnoreCase)
                    || !string.IsNullOrWhiteSpace(p.ApiKey))))
            {
                // 保存的 Models + 本会话实时拉取的 _profileLiveModels（去重）
                var models = (p.Models ?? new List<string>()).ToList();
                if (_profileLiveModels.TryGetValue(p.Id, out var live) && live is not null)
                {
                    models.AddRange(live);
                }
                models = models
                    .Where(m => !string.IsNullOrWhiteSpace(m))
                    .Select(m => m.Trim())
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .ToList();
                // 默认模型不在列表时补一个，保证能点选到默认模型
                if (!string.IsNullOrWhiteSpace(p.Model)
                    && !models.Any(m => string.Equals(m, p.Model, StringComparison.OrdinalIgnoreCase)))
                {
                    models.Add(p.Model.Trim());
                }
                foreach (var m in models)
                {
                    ModelChoices.Add(new ModelChoice($"{m}（{p.Name}）", p, m));
                }
            }
        }
        // 恢复选中：优先同服务商同模型，其次默认组同模型（被去重时落到同端点档案的孪生条目），否则首项
        if (ModelChoices.Count == 0)
        {
            SelectedModelChoice = null;
            return;
        }
        if (currentId is not null)
        {
            SelectedModelChoice = ModelChoices.FirstOrDefault(c =>
                    c.Provider?.Id == currentId && c.Model == currentModel)
                ?? ModelChoices.FirstOrDefault(c => c.Provider?.Id == currentId)
                ?? ModelChoices.FirstOrDefault();
        }
        else if (!string.IsNullOrWhiteSpace(currentModel) && currentModel != DefaultProfileLabel)
        {
            SelectedModelChoice = ModelChoices.FirstOrDefault(c =>
                    c.IsDefault == false && c.Provider is null && c.Model == currentModel)
                ?? ModelChoices.FirstOrDefault(c =>
                    c.IsDefault == false && c.Provider is { } twin
                    && IsSameEndpointAsDefault(twin) && c.Model == currentModel)
                ?? ModelChoices.FirstOrDefault();
        }
        else
        {
            SelectedModelChoice = ModelChoices.FirstOrDefault();
        }
    }

    /// <summary>收集与设置页默认配置指向同一端点的启用档案的全部模型（含各档案默认模型）。
    /// 这些模型在服务商分组里已有「模型名（服务商名）」条目，默认提供商分组不再以裸名重复列出。</summary>
    private HashSet<string> CollectSameEndpointProfileModels()
    {
        var result = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var p in _appSettings.LlmProfiles ?? new List<LlmProfile>())
        {
            if (p is not { IsEnabled: true } || !IsSameEndpointAsDefault(p))
            {
                continue;
            }
            foreach (var m in (p.Models ?? new List<string>()).Append(p.Model))
            {
                if (!string.IsNullOrWhiteSpace(m))
                {
                    result.Add(m.Trim());
                }
            }
        }
        return result;
    }

    /// <summary>档案端点与设置页默认配置是否一致：BaseUrl 归一化（去空白/结尾斜杠，忽略大小写）后比较；
    /// 两边都未填 BaseUrl 时按 provider 类型判断（同为官方默认端点）。</summary>
    private bool IsSameEndpointAsDefault(LlmProfile profile)
    {
        var profileUrl = NormalizeEndpoint(profile.BaseUrl);
        var defaultUrl = NormalizeEndpoint(_appSettings.LlmBaseUrl);
        if (profileUrl.Length > 0 || defaultUrl.Length > 0)
        {
            return string.Equals(profileUrl, defaultUrl, StringComparison.OrdinalIgnoreCase);
        }
        return string.Equals(profile.Provider, _appSettings.LlmProvider, StringComparison.OrdinalIgnoreCase);
    }

    private static string NormalizeEndpoint(string? url) => (url ?? "").Trim().TrimEnd('/');

    private static bool IsKeyOptionalProvider(string? provider) =>
        string.Equals(provider?.Trim(), "ollama", StringComparison.OrdinalIgnoreCase);

    /// <summary>可勾选的知识库集合列表（复选框）。</summary>
    public ObservableCollection<CollectionItem> Collections { get; }

    /// <summary>当前选中的知识库锚定文案（如 @DocMind 或 @技术文档）。</summary>
    public string KnowledgeAnchorText
    {
        get
        {
            var selected = SelectedCollections;
            if (selected.Count == 0) return "@全部知识库";
            if (selected.Count == 1) return $"@{selected[0]}";
            return $"@{selected[0]} +{selected.Count - 1}";
        }
    }

    /// <summary>历史会话列表（持久化在后端 SQLite，重启可恢复）。</summary>
    public ObservableCollection<ChatSessionItem> Sessions { get; }

    private string _sessionSearchFilter = string.Empty;
    private CancellationTokenSource? _sessionSearchCts;

    /// <summary>会话列表搜索关键字（侧边栏即时过滤 + 后端全库搜索）。</summary>
    public string SessionSearchFilter
    {
        get => _sessionSearchFilter;
        set
        {
            if (SetProperty(ref _sessionSearchFilter, value))
            {
                OnPropertyChanged(nameof(FilteredSessions));
                _ = SearchSessionsDebouncedAsync(value);
            }
        }
    }

    /// <summary>输入防抖后请求后端 /v1/chats?q=（标题/消息内容），找回不在 top50 的历史会话。</summary>
    private async Task SearchSessionsDebouncedAsync(string? q)
    {
        _sessionSearchCts?.Cancel();
        var cts = new CancellationTokenSource();
        _sessionSearchCts = cts;
        try
        {
            await Task.Delay(280, cts.Token);
            var search = string.IsNullOrWhiteSpace(q) ? null : q.Trim();
            await LoadSessionsAsync(selectChatId: null, search: search, cancellationToken: cts.Token);
        }
        catch (OperationCanceledException)
        {
            // 输入又变了，丢弃本次
        }
    }

    /// <summary>过滤后的历史会话列表。</summary>
    public IEnumerable<ChatSessionItem> FilteredSessions
    {
        get
        {
            if (string.IsNullOrWhiteSpace(_sessionSearchFilter))
                return Sessions;

            return Sessions.Where(s =>
                (s.Title != null && s.Title.Contains(_sessionSearchFilter, StringComparison.OrdinalIgnoreCase)) ||
                (s.ChatId != null && s.ChatId.Contains(_sessionSearchFilter, StringComparison.OrdinalIgnoreCase)));
        }
    }

    /// <summary>当前是否处于纯本地轻量计算引擎模式。</summary>
    public bool IsLocalComputeEngine =>
        EffectiveProvider.Contains("none", StringComparison.OrdinalIgnoreCase) ||
        EffectiveProvider.Contains("未配置", StringComparison.OrdinalIgnoreCase) ||
        EffectiveProvider.Contains("ollama", StringComparison.OrdinalIgnoreCase) ||
        EffectiveProvider.Contains("local", StringComparison.OrdinalIgnoreCase);

    /// <summary>算力引擎徽章显示文案。</summary>
    public string ComputeEngineBadgeText =>
        IsLocalComputeEngine ? "🛡️ 本地私密引擎" : "⚡ 端云增强推理";

    /// <summary>算力引擎徽章提示。</summary>
    public string ComputeEngineBadgeTip =>
        IsLocalComputeEngine
            ? "当前计算完全在本地完成（ONNX 嵌入模型 + 本地向量库），零数据出机，完全私密安全。"
            : $"已连接云端大模型 ({EffectiveModel})，具备深度推理与复杂格式生成能力。";

    /// <summary>当前选中模型的显示名；由 SelectedModelChoice 单一事实源派生，避免双轨状态不同步。
    /// DefaultModelLabel = 用设置页配置（请求不带 model）。</summary>
    public string SelectedModel =>
        SelectedModelChoice is { IsDefault: false, Model: { } m } && !string.IsNullOrWhiteSpace(m)
            ? m
            : DefaultModelLabel;

    /// <summary>下一条消息实际会用的提供商：选了某服务商模型 → 该服务商名；否则后端全局配置。</summary>
    public string EffectiveProvider =>
        SelectedModelChoice?.Provider is { } p
            ? p.Name
            : string.IsNullOrWhiteSpace(_configuredProvider) || _configuredProvider == "none"
                ? "未配置 LLM"
                : _configuredProvider;

    /// <summary>下一条消息实际会使用的模型，区分默认配置与对话页临时覆盖。</summary>
    public string EffectiveModel =>
        SelectedModel == DefaultModelLabel
            ? (string.IsNullOrWhiteSpace(_configuredModel) ? "未配置模型" : _configuredModel)
            : SelectedModel;

    public string EffectiveModelSummary =>
        $"实际生效: {EffectiveProvider} / {EffectiveModel}" +
        (SelectedModel == DefaultModelLabel ? "（设置页默认）" : "（对话页选择）");

    private ChatSessionItem? _selectedSession;

    /// <summary>当前会话；null = 新会话（未发送过消息）。选中即载入历史消息并可续聊。</summary>
    public ChatSessionItem? SelectedSession
    {
        get => _selectedSession;
        set
        {
            if (SetProperty(ref _selectedSession, value) && value is not null && !_isLoadingSessions)
            {
                // 切换会话前必须先取消进行中的流式请求：否则旧流收尾时会用旧会话 id
                // 覆盖 _chatId 并重载会话列表，把用户刚选中的会话顶掉。
                if (_cts is { IsCancellationRequested: false })
                {
                    try { _cts.Cancel(); } catch { /* 取消失败不阻塞切换 */ }
                }
                _ = LoadSessionMessagesAsync(value);
            }
        }
    }

    /// <summary>当前选中的集合名列表（供发送时使用）。</summary>
    public IReadOnlyList<string> SelectedCollections =>
        Collections.Where(c => c.IsSelected).Select(c => c.Name).ToList();

    /// <summary>当前 Tab 上用于添加集合的临时输入文本。</summary>
    private string _newCollectionName = string.Empty;

    public string NewCollectionName
    {
        get => _newCollectionName;
        set => SetProperty(ref _newCollectionName, value);
    }

    /// <summary>是否至少有一个集合被勾选。</summary>
    public bool HasSelectedCollection => SelectedCollections.Count > 0;

    /// <summary>输入框文本。</summary>
    public string InputText
    {
        get => _inputText;
        set
        {
            if (SetProperty(ref _inputText, value))
            {
                OnPropertyChanged(nameof(HasInput));
                SendCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>待随本次对话发送的附件列表（文档、图片、代码等）。</summary>
    public ObservableCollection<AttachmentItem> PendingAttachments { get; } = [];

    /// <summary>是否有待发送附件。</summary>
    public bool HasPendingAttachments => PendingAttachments.Count > 0;

    /// <summary>是否有输入内容（包含文本或待发附件）。</summary>
    public bool HasInput => !string.IsNullOrWhiteSpace(InputText) || HasPendingAttachments;

    /// <summary>添加附件（打开系统文件选择器）。</summary>
    [RelayCommand]
    private void AddAttachment()
    {
        var dlg = new Microsoft.Win32.OpenFileDialog
        {
            Title = "选择要导入到对话的文档或图片",
            Multiselect = true,
            Filter = "所有支持格式|*.pdf;*.docx;*.doc;*.xlsx;*.xls;*.pptx;*.ppt;*.md;*.markdown;*.html;*.htm;*.txt;*.py;*.cs;*.js;*.ts;*.java;*.go;*.rs;*.cpp;*.c;*.h;*.json;*.yaml;*.yml;*.sql;*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.tiff|" +
                     "文档与表格 (*.pdf,*.docx,*.xlsx,*.pptx,*.md)|*.pdf;*.docx;*.doc;*.xlsx;*.xls;*.pptx;*.ppt;*.md;*.markdown;*.html;*.htm;*.txt|" +
                     "图片与扫描件 (*.png,*.jpg,*.jpeg,*.bmp,*.webp)|*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.tiff|" +
                     "所有文件 (*.*)|*.*"
        };
        if (dlg.ShowDialog() == true)
        {
            AddAttachmentPaths(dlg.FileNames);
        }
    }

    /// <summary>移除待发送附件。</summary>
    [RelayCommand]
    private void RemoveAttachment(AttachmentItem? item)
    {
        if (item != null && PendingAttachments.Remove(item))
        {
            OnPropertyChanged(nameof(HasPendingAttachments));
            OnPropertyChanged(nameof(HasInput));
            SendCommand.NotifyCanExecuteChanged();
        }
    }

    /// <summary>清空待发送附件。</summary>
    [RelayCommand]
    private void ClearAttachments()
    {
        PendingAttachments.Clear();
        OnPropertyChanged(nameof(HasPendingAttachments));
        OnPropertyChanged(nameof(HasInput));
        SendCommand.NotifyCanExecuteChanged();
    }

    /// <summary>清除输入框内容与待发附件（仅作用于本次输入，不影响对话历史）。</summary>
    [RelayCommand]
    private void ClearInput()
    {
        InputText = string.Empty;
        if (PendingAttachments.Count > 0)
        {
            PendingAttachments.Clear();
            OnPropertyChanged(nameof(HasPendingAttachments));
        }
        OnPropertyChanged(nameof(HasInput));
        SendCommand.NotifyCanExecuteChanged();
    }

    /// <summary>批量添加文件路径到待发送附件中（支持拖拽与多选）。</summary>
    public void AddAttachmentPaths(IEnumerable<string> paths)
    {
        foreach (var path in paths)
        {
            if (string.IsNullOrWhiteSpace(path) || !System.IO.File.Exists(path)) continue;
            if (PendingAttachments.Any(a => string.Equals(a.FullPath, path, StringComparison.OrdinalIgnoreCase))) continue;

            var fi = new System.IO.FileInfo(path);
            var ext = fi.Extension.ToLowerInvariant();
            var icon = ext switch
            {
                ".png" or ".jpg" or ".jpeg" or ".bmp" or ".webp" or ".tiff" => "🖼️",
                ".pdf" => "📕",
                ".docx" or ".doc" => "📘",
                ".xlsx" or ".xls" or ".csv" => "📊",
                ".pptx" or ".ppt" => "📙",
                ".md" or ".markdown" or ".txt" => "📝",
                ".py" or ".cs" or ".js" or ".ts" or ".java" or ".go" or ".rs" or ".cpp" or ".c" or ".h" or ".json" or ".sql" => "💻",
                _ => "📎"
            };
            var sizeText = fi.Length < 1024 * 1024 
                ? $"{fi.Length / 1024.0:F1} KB" 
                : $"{fi.Length / (1024.0 * 1024.0):F1} MB";

            PendingAttachments.Add(new AttachmentItem(fi.FullName, fi.Name, icon, sizeText));
        }
        OnPropertyChanged(nameof(HasPendingAttachments));
        OnPropertyChanged(nameof(HasInput));
        SendCommand.NotifyCanExecuteChanged();
    }

    /// <summary>是否正在请求中。</summary>
    public bool IsBusy
    {
        get => _isBusy;
        set
        {
            if (SetProperty(ref _isBusy, value))
            {
                OnPropertyChanged(nameof(ShowEmptyGuide));
                OnPropertyChanged(nameof(ShowStop));
                SendCommand.NotifyCanExecuteChanged();
                StopCommand.NotifyCanExecuteChanged();
                RegenerateCommand.NotifyCanExecuteChanged();
                ContinueWritingCommand.NotifyCanExecuteChanged();
                UpdateMessageFlags();
            }
        }
    }

    /// <summary>状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>从后端拉取已有知识库集合，并合并用户手动添加的。</summary>
    public IAsyncRelayCommand LoadCollectionsCommand { get; }

    /// <summary>添加一个新的知识库集合（仅本地勾选，发送时随请求带上）。</summary>
    public IAsyncRelayCommand<string?> AddCollectionCommand { get; }

    private void OnCollectionItemChanged(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(CollectionItem.IsSelected))
        {
            OnPropertyChanged(nameof(HasSelectedCollection));
            OnPropertyChanged(nameof(KnowledgeAnchorText));
            SendCommand.NotifyCanExecuteChanged();
            // 勾选/取消即落盘，重启后恢复（与联网搜索开关同机制）
            PersistCollectionSelection();
        }
    }

    /// <summary>构造期间/刷新期间抑制勾选落盘（避免重建列表的中间状态反复写盘）。</summary>
    private bool _isRestoringCollections;

    /// <summary>把对话页勾选的知识库集合落盘（AppSettings.LastChatCollections），重启后恢复。
    /// 加载/恢复期间（_isRestoringCollections）跳过，由加载流程结束后统一落一次终态。</summary>
    private void PersistCollectionSelection()
    {
        if (_isRestoringCollections)
        {
            return;
        }
        _appSettings.LastChatCollections = SelectedCollections.ToList();
        try
        {
            _appSettings.Save();
        }
        catch
        {
            // 落盘失败不阻断对话
        }
    }

    /// <summary>是否仍需用落盘的勾选（AppSettings.LastChatCollections）恢复一次选中状态。
    /// 仅首次加载生效，之后的刷新只保留本会话内的运行时勾选（避免覆盖用户当次的选择）。</summary>
    private bool _collectionsSeedPending = true;

    private async Task LoadCollectionsAsync()
    {
        _isRestoringCollections = true;
        try
        {
            var stats = await _apiService.GetStatsAsync();
            var names = stats.Collections.Keys.ToHashSet();

            // 保留用户已手动添加、但后端尚不存在的集合
            foreach (var existing in Collections.ToList())
            {
                if (!names.Contains(existing.Name))
                {
                    names.Add(existing.Name);
                }
            }

            // 记录当前勾选状态，刷新后恢复，避免自动刷新丢失用户选择
            var selectedNames = Collections
                .Where(c => c.IsSelected)
                .Select(c => c.Name)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);

            // 首次加载：用上次落盘的勾选恢复（重启后保持同样的知识库选择）
            if (_collectionsSeedPending)
            {
                _collectionsSeedPending = false;
                foreach (var saved in _appSettings.LastChatCollections)
                {
                    if (!string.IsNullOrWhiteSpace(saved))
                    {
                        selectedNames.Add(saved);
                    }
                }
                // 只保留后端仍存在的集合：落盘的集合全部被删除时退回默认勾选 default
                selectedNames.IntersectWith(names);
            }

            Collections.Clear();
            var list = names.ToList();
            list.Sort(StringComparer.OrdinalIgnoreCase);
            foreach (var name in list)
            {
                // 已有勾选则恢复；无任何勾选时（首次加载且无落盘记录）默认选 default
                var isSelected = selectedNames.Contains(name)
                    || (selectedNames.Count == 0 && string.Equals(name, "default", StringComparison.OrdinalIgnoreCase));
                Collections.Add(new CollectionItem { Name = name, IsSelected = isSelected });
            }

            OnPropertyChanged(nameof(HasSelectedCollection));
            SendCommand.NotifyCanExecuteChanged();
            DebugLog.Info($"集合列表加载完成: {list.Count} 个 [{string.Join(", ", list)}]", "Chat");
            // 终态统一落盘一次（幂等）；放在成功路径，加载失败不吞掉用户已有落盘记录
            PersistCollectionSelection();
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"加载集合列表失败: {ex.Message}", "Chat");
            // 失败时至少保证有一个 default 可选
            if (Collections.Count == 0)
            {
                Collections.Add(new CollectionItem { Name = "default", IsSelected = true });
            }
        }
        finally
        {
            _isRestoringCollections = false;
        }
    }

    private async Task AddCollectionAsync(string? name)
    {
        var trimmed = (name ?? NewCollectionName).Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return;
        }

        _isRestoringCollections = true;
        try
        {
            // 真正在后端创建一个新的空知识库集合，并刷新列表
            var stats = await _apiService.CreateCollectionAsync(trimmed);
            var names = stats.Collections.Keys.ToHashSet();

            // 保留用户已手动添加但后端尚不存在的集合（理论上创建后已存在）
            foreach (var existing in Collections.ToList())
            {
                if (!names.Contains(existing.Name))
                {
                    names.Add(existing.Name);
                }
            }

            Collections.Clear();
            var list = names.ToList();
            list.Sort(StringComparer.OrdinalIgnoreCase);
            foreach (var n in list)
            {
                var isSelected = string.Equals(n, trimmed, StringComparison.OrdinalIgnoreCase)
                                 || string.Equals(n, "default", StringComparison.OrdinalIgnoreCase);
                Collections.Add(new CollectionItem { Name = n, IsSelected = isSelected });
            }

            StatusMessage = $"已创建知识库：{trimmed}";
            DebugLog.Info($"创建知识库集合成功: {trimmed}", "Chat");
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"创建知识库集合失败: {ex.Message}", "Chat");
            StatusMessage = $"创建失败：{ex.Message}";
            // 失败时不阻塞用户：仍把该集合加入本地列表并勾选，便于重试或离线使用
            if (!Collections.Any(c => string.Equals(c.Name, trimmed, StringComparison.OrdinalIgnoreCase)))
            {
                Collections.Add(new CollectionItem { Name = trimmed, IsSelected = true });
            }
        }
        finally
        {
            _isRestoringCollections = false;
        }

        NewCollectionName = string.Empty;
        OnPropertyChanged(nameof(HasSelectedCollection));
        SendCommand.NotifyCanExecuteChanged();
        // 新建集合已勾选 → 落盘终态，重启后同样恢复
        PersistCollectionSelection();
    }

    /// <summary>对话消息列表。</summary>
    public ObservableCollection<ChatMessage> Messages { get; } = [];

    /// <summary>是否有消息。</summary>
    public bool HasMessages => Messages.Count > 0;

    /// <summary>无消息时显示空态。</summary>
    public bool ShowEmptyGuide => !IsBusy && Messages.Count == 0;

    /// <summary>当前会话消息数（工具条展示，如「💬 8 条消息」；无消息返回空串隐藏）。</summary>
    public string MessagesCountText =>
        Messages.Count == 0 ? string.Empty : $"💬 {Messages.Count} 条消息";

    /// <summary>LLM 是否已可用：对话页选中了某服务商档案的模型（发送时按请求携带 ProviderConfig，
    /// 不依赖全局配置），或设置页全局 provider 已配置（非 none）。
    /// 空态引导与发送前事前拦截共用此判断。</summary>
    public bool IsLlmConfigured =>
        SelectedModelChoice?.Provider is not null
        || (_defaultKeyConfigured && !string.IsNullOrWhiteSpace(_configuredProvider) && _configuredProvider != "none");

    /// <summary>空态引导文案：LLM 未配置时优先引导配置（事前引导），已配置时引导导入与提问。</summary>
    public string EmptyGuideText =>
        IsLlmConfigured
            ? "把问题丢进你的文献库。\n\n"
              + "DocMind 会先在已导入文档里做混合检索，\n"
              + "再结合上下文给出带 [N] 出处的回答。\n\n"
              + "库还空着？先去【导入】丢几份文件进来。"
            : "还差一步：接上推理模型。\n\n"
              + "到【设置 → AI 模型与对话】：\n"
              + "  1. 选服务商（OpenAI 兼容 / Claude / Gemini / Ollama）\n"
              + "  2. 填 Key 后点「测试连接」\n"
              + "  3. 回来就能开问\n\n"
              + "完全离线：选 Ollama，本机推理、无需 Key。";

    private bool CanSend => !IsBusy && HasInput;

    /// <summary>是否可重新生成（非生成中，且最后一条是 assistant 消息）。</summary>
    private bool CanRegenerate => !IsBusy && Messages.Count > 0 && Messages[^1].Role == "assistant";

    /// <summary>是否可停止（生成中）。</summary>
    private bool CanStop => IsBusy;

    /// <summary>停止命令是否可见（生成中显示停止按钮）。</summary>
    public bool ShowStop => IsBusy;

    /// <summary>发送消息（流式）。</summary>
    [RelayCommand(CanExecute = nameof(CanSend))]
    private async Task SendAsync()
    {
        var query = InputText.Trim();
        if (string.IsNullOrWhiteSpace(query))
        {
            if (HasPendingAttachments)
            {
                query = "请分析并总结我上传的附件内容。";
            }
            else
            {
                return;
            }
        }

        await SendCoreAsync(query, addUserMessage: true);
    }

    /// <summary>点击推荐行动胶囊：将建议填入并直接发送问答。</summary>
    [RelayCommand]
    private async Task ExecuteActionAsync(string? action)
    {
        if (string.IsNullOrWhiteSpace(action) || IsBusy)
            return;

        var query = action.Trim();
        const string prefix = "👉";
        if (query.StartsWith(prefix, StringComparison.Ordinal))
        {
            query = query[prefix.Length..].Trim();
        }

        InputText = query;
        await SendAsync();
    }

    // --- 沉淀入库弹窗微调状态 (方案 A + B) ---
    private bool _isIngestDialogOpen;
    private string _ingestDialogTitle = string.Empty;
    private string _ingestDialogContent = string.Empty;
    private string _ingestDialogCollection = "default";
    private string _ingestDialogTags = string.Empty;
    private bool _isDialogIngesting;
    private ChatMessage? _currentIngestingMessage;

    public bool IsIngestDialogOpen
    {
        get => _isIngestDialogOpen;
        set => SetProperty(ref _isIngestDialogOpen, value);
    }

    public string IngestDialogTitle
    {
        get => _ingestDialogTitle;
        set => SetProperty(ref _ingestDialogTitle, value);
    }

    public string IngestDialogContent
    {
        get => _ingestDialogContent;
        set => SetProperty(ref _ingestDialogContent, value);
    }

    public string IngestDialogCollection
    {
        get => _ingestDialogCollection;
        set => SetProperty(ref _ingestDialogCollection, value);
    }

    public string IngestDialogTags
    {
        get => _ingestDialogTags;
        set => SetProperty(ref _ingestDialogTags, value);
    }

    public bool IsDialogIngesting
    {
        get => _isDialogIngesting;
        set => SetProperty(ref _isDialogIngesting, value);
    }

    /// <summary>打开沉淀入库微调弹窗（支持整条消息或划词片段）。</summary>
    [RelayCommand]
    public void OpenIngestDialog(object? param)
    {
        string contentToIngest = string.Empty;
        _currentIngestingMessage = null;

        if (param is ChatMessage msg)
        {
            _currentIngestingMessage = msg;
            contentToIngest = msg.Content.Trim();
            var actionIdx = contentToIngest.IndexOf("[ACTIONS:", StringComparison.OrdinalIgnoreCase);
            if (actionIdx >= 0)
            {
                contentToIngest = contentToIngest[..actionIdx].Trim();
            }
        }
        else if (param is string str && !string.IsNullOrWhiteSpace(str))
        {
            contentToIngest = str.Trim();
        }

        if (string.IsNullOrWhiteSpace(contentToIngest))
            return;

        // 提取首行作为推荐标题
        var firstLine = contentToIngest.Split('\n', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? contentToIngest;
        firstLine = System.Text.RegularExpressions.Regex.Replace(firstLine, @"^[#\s\-*📌💡]+", "").Trim();
        var initialTitle = firstLine.Length > 28 ? firstLine[..28] + "…" : firstLine;
        if (string.IsNullOrWhiteSpace(initialTitle))
        {
            initialTitle = $"知识探讨沉淀 ({DateTime.Now:MM-dd HH:mm})";
        }

        IngestDialogTitle = initialTitle;
        IngestDialogContent = contentToIngest;
        IngestDialogTags = "探讨笔记, 精炼结论";

        var targetCol = SelectedCollections.FirstOrDefault() ?? "default";
        IngestDialogCollection = targetCol;

        IsIngestDialogOpen = true;
    }

    /// <summary>确认沉淀入库（带用户微调后的标题、集合与正文）。</summary>
    [RelayCommand]
    public async Task ConfirmIngestDialogAsync()
    {
        if (string.IsNullOrWhiteSpace(IngestDialogContent) || IsDialogIngesting)
            return;

        IsDialogIngesting = true;
        if (_currentIngestingMessage != null)
        {
            _currentIngestingMessage.IsIngesting = true;
        }
        StatusMessage = "正在沉淀入库…";

        try
        {
            var text = IngestDialogContent.Trim();
            var title = string.IsNullOrWhiteSpace(IngestDialogTitle) ? "知识沉淀笔记" : IngestDialogTitle.Trim();
            var collection = string.IsNullOrWhiteSpace(IngestDialogCollection) ? "default" : IngestDialogCollection.Trim();

            // 若有标签，追加到正文顶部
            if (!string.IsNullOrWhiteSpace(IngestDialogTags))
            {
                var tagList = IngestDialogTags.Split(new[] { ',', ' ', '，', ';' }, StringSplitOptions.RemoveEmptyEntries)
                    .Select(t => t.StartsWith("#") ? t : $"#{t}")
                    .ToList();
                if (tagList.Count > 0)
                {
                    text = $"【标签】：{string.Join(" ", tagList)}\n\n{text}";
                }
            }

            var req = new IngestTextRequest
            {
                Text = text,
                Title = $"💡 {title}",
                Collection = collection,
                Force = true
            };

            await _apiService.IngestTextAsync(req);

            if (_currentIngestingMessage != null)
            {
                _currentIngestingMessage.IsIngesting = false;
                _currentIngestingMessage.IsIngested = true;
            }

            IsIngestDialogOpen = false;
            _notifications?.Success($"已成功沉淀至集合「{collection}」", "沉淀入库");
            StatusMessage = $"已沉淀入库：{title}";
            DebugLog.Info($"沉淀入库成功: title={title}, collection={collection}", "Chat");

            // 异步刷新知识库集合
            _ = LoadCollectionsAsync();
        }
        catch (Exception ex)
        {
            if (_currentIngestingMessage != null)
            {
                _currentIngestingMessage.IsIngesting = false;
            }
            _notifications?.Error($"沉淀入库失败: {ex.Message}", "错误");
            StatusMessage = $"沉淀入库失败：{ex.Message}";
            DebugLog.Error($"沉淀入库异常: {ex}", "Chat");
        }
        finally
        {
            IsDialogIngesting = false;
        }
    }

    /// <summary>关闭沉淀入库弹窗。</summary>
    [RelayCommand]
    public void CloseIngestDialog()
    {
        IsIngestDialogOpen = false;
        if (_currentIngestingMessage != null && !IsDialogIngesting)
        {
            _currentIngestingMessage.IsIngesting = false;
        }
    }

    /// <summary>一键将回答沉淀为知识笔记入库（快捷入口，直接打开微调弹窗）。</summary>
    [RelayCommand]
    private void IngestMessage(ChatMessage? message)
    {
        OpenIngestDialog(message);
    }

    /// <summary>插入场景化快捷指令模板。</summary>
    [RelayCommand]
    private void InsertPromptTemplate(string? templateType)
    {
        var prefix = templateType switch
        {
            "ppt" => "请依托上述知识库资料，为我制作一份结构完整、逻辑清晰的专业汇报 PPT 演示文稿（包含封面、目录、核心论点与每页演讲备注）：\n",
            "report" => "请依托上述知识库资料，撰写一份结构严谨、包含方案论证与对比表格的技术研报/项目方案公文：\n",
            "lesson" => "请依托上述教材与资料，设计一份系统完整的课程教学大纲与分课时教案（含教学目标、重难点、教学过程、随堂测验）：\n",
            "matrix" => "请全方位提炼抽取相关方案与特性的参数指标，输出结构化的多维对比矩阵与评估表格：\n",
            "webpage" => "请为上述知识主题生成一个高颜值、自包含、带指标卡片与知识详情的交互式单文件 HTML 总结看板：\n",
            "summary" => "请将上述内容萃取提炼为核心结论与清晰的 Action Items 待办清单：\n",
            "table" => "请以结构化 Markdown 表格形式，全方位对比各方案的优缺点、适用场景与成本效益：\n",
            "polish" => "请将以下草稿按严谨专业的企业公文与技术汇报规范进行润色重构：\n",
            "pitfall" => "请对以下方案进行专家级把关评审，列出潜在风险点、性能隐患与避坑防范建议：\n",
            "faq" => "请基于上述知识库资料，提炼整理一份高频 FAQ 常见问题解答手册（每个问题附带清晰、简洁、可直接执行的回答）：\n",
            "swot" => "请对上述主题或方案进行深入的 SWOT 分析（优势、劣势、机会、威胁），并据此给出战略建议与落地行动项：\n",
            "roadmap" => "请基于上述知识库资料，设计一份清晰的项目/产品发展路线图（按阶段划分里程碑、关键产出物、负责人与时间节点）：\n",
            "case" => "请从知识库中提取关键案例，撰写一份结构化的成功案例分析报告（背景、挑战、方案、成效、可复制经验）：\n",
            "mindmap" => "请对上述内容进行结构化梳理，输出一份层次分明的思维导图大纲（核心主题→子主题→关键要点，用 Markdown 缩进表示层级）：\n",
            "interview" => "请基于上述资料，生成一份深度访谈/专家问答稿（含背景介绍、核心问题链、追问逻辑与总结要点）：\n",
            _ => string.Empty
        };

        // 联动自动切换对应创作人设
        var matchedPersonaId = templateType switch
        {
            "ppt" => "ppt",
            "report" => "doc",
            "lesson" => "lesson",
            "matrix" => "table",
            "webpage" => "web",
            _ => null
        };
        if (matchedPersonaId != null)
        {
            var p = AvailablePersonas.FirstOrDefault(x => x.Id == matchedPersonaId);
            if (p != null) SelectedPersona = p;
        }

        if (string.IsNullOrEmpty(InputText))
        {
            InputText = prefix;
        }
        else
        {
            InputText = prefix + InputText;
        }
    }

    // ==================== PPT 定制偏好（创作前置征询） ====================
    // 设计意图：把「用途 / 篇幅 / 配色 / 必含要点」等创作决策前移到生成之前，
    // 避免 LLM 先自由发挥、用户只能在事后用主题下拉补救（事后微调成本高且难救回内容结构）。
    private bool _isPptPrefsOpen;
    private string _pptPurpose = "工作汇报评审";
    private string _pptLength = "标准 10-14 页";
    private string _pptMustInclude = string.Empty;
    private PptThemeOption? _pptPrefTheme;

    /// <summary>PPT 定制偏好弹窗是否展开。</summary>
    public bool IsPptPrefsOpen
    {
        get => _isPptPrefsOpen;
        set => SetProperty(ref _isPptPrefsOpen, value);
    }

    /// <summary>可选用途场景。</summary>
    public IReadOnlyList<string> PptPurposeOptions { get; } = new[]
    {
        "工作汇报评审", "客户提案 / 商务推介", "课程教学 / 培训", "项目技术方案", "通用知识分享"
    };

    /// <summary>选中的用途场景。</summary>
    public string PptPurpose
    {
        get => _pptPurpose;
        set => SetProperty(ref _pptPurpose, value);
    }

    /// <summary>可选篇幅档位。</summary>
    public IReadOnlyList<string> PptLengthOptions { get; } = new[]
    {
        "精简 6-8 页", "标准 10-14 页", "详尽 16-22 页"
    };

    /// <summary>选中的篇幅档位。</summary>
    public string PptLength
    {
        get => _pptLength;
        set => SetProperty(ref _pptLength, value);
    }

    /// <summary>必须包含的内容要点（选填）。</summary>
    public string PptMustInclude
    {
        get => _pptMustInclude;
        set => SetProperty(ref _pptMustInclude, value);
    }

    /// <summary>偏好的主题配色（会写进 artifact 的 theme 属性，决定前端预览配色）。</summary>
    public PptThemeOption? PptPrefTheme
    {
        get => _pptPrefTheme;
        set => SetProperty(ref _pptPrefTheme, value);
    }

    /// <summary>打开 PPT 定制偏好弹窗（只征询，不立即发送）。</summary>
    [RelayCommand]
    private void OpenPptPrefs()
    {
        PptPrefTheme ??= SelectedTheme;
        PptMustInclude = string.Empty;
        IsPptPrefsOpen = true;
    }

    /// <summary>放弃定制。</summary>
    [RelayCommand]
    private void CancelPptPrefs()
    {
        IsPptPrefsOpen = false;
    }

    /// <summary>确认定制：把偏好编译成强约束提示词填入输入框，由用户审阅后再自行发送。</summary>
    [RelayCommand]
    private void ConfirmPptPrefs()
    {
        var theme = PptPrefTheme ?? SelectedTheme;
        var prompt = BuildPptPrompt(theme);
        InputText = string.IsNullOrWhiteSpace(InputText)
            ? prompt
            : InputText.TrimEnd() + "\n\n" + prompt;
        IsPptPrefsOpen = false;
        StatusMessage = "已生成 PPT 定制提示词，确认无误后发送即可";
    }

    /// <summary>把定制偏好编译为一份结构化提示词。其中的格式约定与前端
    /// ArtifactRegex 及幻灯片切片解析规则严格对应，确保产出可被工作台正确渲染成幻灯片。</summary>
    private string BuildPptPrompt(PptThemeOption theme)
    {
        var sb = new System.Text.StringBuilder();
        sb.AppendLine("请依托上述知识库资料，为我制作一份专业汇报 PPT 演示文稿。");
        sb.AppendLine();
        sb.AppendLine("【创作要求】");
        sb.AppendLine($"- 用途场景：{PptPurpose}");
        sb.AppendLine($"- 篇幅要求：{PptLength}（须含封面页与目录页）");
        sb.AppendLine($"- 配色主题：{theme.DisplayName}（theme id 固定为 {theme.Id}）");
        if (!string.IsNullOrWhiteSpace(PptMustInclude))
        {
            sb.AppendLine($"- 必须包含以下内容要点：{PptMustInclude.Trim()}");
        }
        sb.AppendLine();
        sb.AppendLine("【输出格式硬性要求】");
        sb.AppendLine($"1. 整份内容必须用 :::artifact type=\"pptx\" title=\"...\" theme=\"{theme.Id}\" 包裹，结尾用 ::: 收束；theme 必须原样写 {theme.Id}，不要替换成其他主题。");
        sb.AppendLine("2. 每一页之间用单独一行的 --- 分隔。");
        sb.AppendLine("3. 每页第一行用 `# 页面标题`；封面页可再补一行 `## 副标题`。");
        sb.AppendLine("4. 正文要点每行以 `- ` 开头，单页 3-6 条，避免文字过密。");
        sb.AppendLine("5. 每页末尾用 `<!-- note: 本页演讲备注 -->` 给出演讲者要说的话。");
        sb.AppendLine("6. 并列模块用 `### 卡片标题` 生成卡片板式；数据指标页用 `<!-- layout: metrics -->`。");
        sb.AppendLine("7. 除备注外不要输出额外解释文字，全部内容放进 artifact 块内。");
        return sb.ToString();
    }

    /// <summary>重新生成最后一条回答：移除末尾 assistant 消息后重发最后一条用户问题（保留 chatId 多轮上下文）。</summary>
    [RelayCommand(CanExecute = nameof(CanRegenerate))]
    private async Task RegenerateAsync()
    {
        if (IsBusy)
            return;

        var lastUserIdx = -1;
        for (var i = Messages.Count - 1; i >= 0; i--)
        {
            if (Messages[i].Role == "user")
            {
                lastUserIdx = i;
                break;
            }
        }
        if (lastUserIdx < 0)
            return;

        var query = Messages[lastUserIdx].Content;
        // 移除该用户消息之后的所有消息（旧回答/错误占位）
        for (var i = Messages.Count - 1; i > lastUserIdx; i--)
        {
            Messages.RemoveAt(i);
        }
        DebugLog.Info($"重新生成: 移除 {Messages.Count - lastUserIdx - 1} 条旧回答后重发", "Chat");
        await SendCoreAsync(query, addUserMessage: false);
    }

    /// <summary>是否可继续写（最后一条 assistant 存在且非生成中）。</summary>
    private bool CanContinueWriting()
        => !IsBusy && Messages.Any(m => m.Role == "assistant" && !string.IsNullOrEmpty(m.Content));

    /// <summary>续写最后一条回答（P0）：不新开用户气泡，向最后一条 assistant 追加正文。
    /// 输入框若有内容则作为补充要求；否则用默认「继续写」。</summary>
    [RelayCommand(CanExecute = nameof(CanContinueWriting))]
    private async Task ContinueWritingAsync()
    {
        if (IsBusy)
            return;
        var extra = InputText;
        InputText = string.Empty;
        OnPropertyChanged(nameof(HasInput));
        SendCommand.NotifyCanExecuteChanged();
        var query = string.IsNullOrWhiteSpace(extra) ? "继续写" : extra.Trim();
        DebugLog.Info($"继续写: extraLen={query.Length} msgCount={Messages.Count}", "Chat");
        await SendCoreAsync(query, addUserMessage: false, continueWriting: true);
    }

    /// <summary>发送核心：添加用户消息（可选）+ 流式请求 + 终帧回写。Send 与 Regenerate/Continue 共用。</summary>
    private async Task SendCoreAsync(string query, bool addUserMessage, bool continueWriting = false)
    {
        // 事前拦截：LLM 未配置时直接引导配置，不发注定失败的请求（覆盖发送/快捷提问/重新生成入口）
        if (!IsLlmConfigured)
        {
            StatusMessage = "尚未配置大模型：请到【设置 → 大模型对话】完成配置后重试";
            _notifications?.Warning("尚未配置大模型，无法开始对话：请到【设置 → 大模型对话】完成配置", "需要配置");
            return;
        }

        // 提取附件列表
        var attachments = PendingAttachments.Select(a => a.FullPath).ToList();
        var attachLabels = PendingAttachments.Select(a => $"{a.Icon} {a.FileName}").ToList();
        PendingAttachments.Clear();
        OnPropertyChanged(nameof(HasPendingAttachments));
        OnPropertyChanged(nameof(HasInput));
        SendCommand.NotifyCanExecuteChanged();

        ChatMessage assistantMsg;
        if (continueWriting)
        {
            // 续写：复用最后一条 assistant，不新开气泡
            var lastAssistant = Messages.LastOrDefault(m => m.Role == "assistant");
            if (lastAssistant is null)
            {
                StatusMessage = "没有可续写的回答";
                return;
            }
            assistantMsg = lastAssistant;
            assistantMsg.IsLoading = true;
            assistantMsg.IsWaitingForFirstToken = false;
            assistantMsg.Truncated = false;
            assistantMsg.ShowContinueWriting = false;
        }
        else
        {
            // 添加用户消息
            if (addUserMessage)
            {
                var userDisplay = query;
                if (attachLabels.Count > 0)
                {
                    userDisplay = $"[📎 附件: {string.Join(", ", attachLabels)}]\n{query}";
                }
                var userMsg = new ChatMessage { Role = "user", Content = userDisplay };
                AttachFeedbackSink(userMsg);
                Messages.Add(userMsg);
            }

            // 添加流式占位（先空内容，逐 token 追加）
            // 模式快照：发出时把当前模式写进消息，历史渲染只读本消息字段，
            // 不读全局开关（中途切模式不影响历史消息渲染）
            assistantMsg = new ChatMessage { Role = "assistant", Content = "", IsLoading = true, IsWaitingForFirstToken = true, AnswerFormat = IsHtmlAnswerMode ? "html" : "markdown" };
            AttachFeedbackSink(assistantMsg);
            Messages.Add(assistantMsg);
        }

        // 发送开始前，清空输入框（续写命令已清空）
        if (!continueWriting)
        {
            InputText = string.Empty;
        }

        // 先建取消令牌再置 IsBusy：消除「停止按钮已可用但 _cts 尚未创建」的竞态空窗
        _cts?.Dispose();
        _cts = new CancellationTokenSource();
        var cts = _cts;

        IsBusy = true;
        ShowStopChanged();
        StatusMessage = "对话中…";
        var sw = System.Diagnostics.Stopwatch.StartNew();

        var timerCts = new CancellationTokenSource();
        _ = Task.Run(async () =>
        {
            try
            {
                while (!timerCts.Token.IsCancellationRequested && assistantMsg.IsLoading && (assistantMsg.IsThinkingInProgress || assistantMsg.IsWaitingForFirstToken))
                {
                    await Task.Delay(100, timerCts.Token).ConfigureAwait(false);
                    if (timerCts.Token.IsCancellationRequested) break;
                    var curMs = sw.ElapsedMilliseconds;
                    Application.Current?.Dispatcher?.InvokeAsync(() =>
                    {
                        assistantMsg.UpdateLiveThinkingDuration(curMs);
                    });
                }
            }
            catch { /* 取消时静默退出 */ }
        }, timerCts.Token);

        // 流式排查统计：token 帧数 + 首 token 延迟（TTFT）
        var tokenCount = 0;
        long firstTokenMs = -1;
        // 本条消息是否已落地过首个正文 token。
        // 不能用 IsWaitingForFirstToken / IsThinkingInProgress 代替：这两个标志会被
        // onStatus/onThinking 回调反复置 True（模型推理链、上下文溢出重试状态帧、
        // 生成结束后的 Agent 自省帧），流中途一旦复位，下一个 token 就会把已累积的
        // 正文整体覆盖，气泡里只剩该帧之后的内容（表现为「回答只剩一个标题」）。
        // 续写：已有正文时必须从追加开始，绝不能被首 token 覆盖。
        var firstTokenApplied = continueWriting && !string.IsNullOrEmpty(assistantMsg.Content);
        try
        {
            var selected = SelectedCollections;
            DebugLog.Info(
                $"发送消息: query='{(query.Length > 100 ? query[..100] + "…" : query)}' " +
                $"collections=[{string.Join(",", selected)}] chatId='{_chatId ?? "-"}' model='{(SelectedModel == DefaultModelLabel ? "-" : SelectedModel)}' persona='{SelectedPersona?.Id ?? "-"}' msgCount={Messages.Count} attachCount={attachments.Count} continue={continueWriting}",
                "Chat");
            ChatStreamResult? final = null;

            // ── Phase 1: 记忆注入 ──
            // 在发送前搜索相关记忆，作为独立字段 MemoryContext 传给后端。
            // 绝不拼进 Query：否则污染检索、会话历史，并让弱模型把记忆主题
            // 当成用户本轮问题（真实故障：问 GPT 答成豆包）。
            var memoryContext = string.Empty;
            if (_userMemory is { } mem && _appSettings.MemoryEnabled)
            {
                try
                {
                    memoryContext = await mem.BuildSystemPromptInjectionAsync(query);
                }
                catch (Exception ex)
                {
                    DebugLog.Warn($"记忆搜索失败（不阻断对话）: {ex.Message}", "Memory");
                }
            }
            // 正文引用角标 [n] 点击 → 打开对应来源抽屉
            assistantMsg.SourceMarkerRequested += index =>
            {
                var src = assistantMsg.Sources?.FirstOrDefault(s => s.Index == index);
                if (src is not null)
                {
                    OpenSource(src);
                }
            };
            // 创作物解析完成 → 自动导出物理文件
            assistantMsg.ArtifactParsed += (_, artifact) =>
            {
                if (!assistantMsg.IsLoading)  // 仅在流式完成时触发
                {
                    _ = AutoExportArtifactAsync(artifact);
                }
            };
            // 发送时按选中模型项构造请求：
            //  - 默认项 → 不带 ProviderConfig / Model（用后端全局配置）
            //  - 选中某服务商的模型 → 携带该服务商配置（provider/key/url/模型），按请求生效、不污染全局
            var choice = SelectedModelChoice;
            var isDefaultChoice = choice is null || choice.IsDefault;
            var choiceProvider = choice?.Provider;
            var choiceModel = isDefaultChoice ? null : (choice?.Model ?? SelectedModel);
            await _apiService.ChatStreamAsync(
                new ChatRequest
                {
                    Query = query,
                    Collections = selected.Count > 0 ? selected : null,
                    // TopK 不传（null）：由后端按设置页的 rag_top_k 决定，
                    // 避免对话页硬编码 5 覆盖用户配置（此前设置页改引用数无效）
                    TopK = null,
                    ChatId = _chatId,
                    // 对话页快速切换模型：默认项不带（用设置页配置），选了具体模型则按请求覆盖
                    Model = choiceModel,
                    // 点选某服务商的模型时，携带该服务商配置（provider/key/url/模型），按请求生效
                    ProviderConfig = choiceProvider is null || isDefaultChoice
                        ? null
                        : new ProviderConfig
                        {
                            Provider = choiceProvider.Provider,
                            ApiKey = choiceProvider.ApiKey,
                            BaseUrl = choiceProvider.BaseUrl,
                            Model = choiceModel,
                        },
                    Persona = SelectedPersona?.Id,
                    PersonaPrompt = SelectedPersona is { IsCustom: true }
                        ? (string.IsNullOrWhiteSpace(SelectedPersona.Description)
                            ? SelectedPersona.DisplayName
                            : SelectedPersona.Description)
                        : null,
                    EnableWebSearch = IsWebSearchEnabled,
                    WebSearchMode = IsWebSearchEnabled ? SelectedWebSearchMode.Key : null,
                    Attachments = attachments.Count > 0 ? attachments : null,
                    // 本机用户自己的 GitHub Token（联网搜索 GitHub 通道按请求携带，
                    // 后端不共享、不落盘为全局配置），留空 = 用匿名公开额度
                    GithubToken = string.IsNullOrWhiteSpace(_appSettings.GithubToken)
                        ? null
                        : _appSettings.GithubToken.Trim(),
                    RagMode = _appSettings.RagMode,
                    MemoryContext = string.IsNullOrWhiteSpace(memoryContext) ? null : memoryContext,
                    // P0 双轨：续写/创作人设走 delivery，其余交给后端按意图自动推断
                    ResponseMode = continueWriting || (SelectedPersona?.Id is "ppt" or "doc" or "lesson" or "table" or "web")
                        ? "delivery"
                        : null,
                    ContinueWriting = continueWriting,
                    // 回答模式：会话内选择优先；chatMode 供后端 resolve_chat_mode 路由。
                    // AgentMode 兼容标志受总闸约束（AgentModeEnabled 关闭时不声明，
                    // 基础产品不暴露 Agent 能力；后端仍按 chatMode 回落 RAG）
                    ChatMode = continueWriting ? "rag" : SelectedChatMode.Key,
                    AgentMode = !continueWriting && SelectedChatMode.Key == "agent" && _appSettings.AgentModeEnabled,
                    // 渲染轨：HTML 体验模式随请求下发；markdown 走后端默认（null 透传）
                    AnswerFormat = IsHtmlAnswerMode ? "html" : null,
                },
                onToken: token =>
                {
                    if (tokenCount == 0)
                    {
                        firstTokenMs = sw.ElapsedMilliseconds;
                        try { timerCts.Cancel(); } catch { }
                    }
                    tokenCount++;

                    void ApplyToken()
                    {
                        // 只在真正的首帧做一次「重置」（首帧前 Content 是空占位）；
                        // 此后一律追加，任何 status/thinking 帧都不得再清空已累积的正文。
                        if (!firstTokenApplied)
                        {
                            firstTokenApplied = true;
                            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
                            assistantMsg.Content = token;
                        }
                        else
                        {
                            assistantMsg.Content += token;
                        }
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyToken);
                    }
                    else
                    {
                        ApplyToken();
                    }
                },
                onStatus: status =>
                {
                    void ApplyStatus()
                    {
                        // 收集为「思考过程」步骤（检索/联网搜索/生成…），供折叠区展示；
                        assistantMsg.IsThinkingInProgress = true;
                        assistantMsg.UpdateLiveThinkingDuration(sw.ElapsedMilliseconds);
                        assistantMsg.AddThinkingStep(status ?? "");
                        assistantMsg.ShowStatus = true;
                        assistantMsg.StatusText = status ?? "";
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyStatus);
                    }
                    else
                    {
                        ApplyStatus();
                    }
                },
                onAgentEvent: (kind, detail) =>
                {
                    void ApplyAgent()
                    {
                        // T7：Agent 轨迹进入思考折叠区；低价值结果收成短标签，避免时间线被「无变化」淹没
                        var label = kind switch
                        {
                            "agent_plan" => $"Agent 规划：{detail}",
                            "tool_call" => $"调用工具 {detail}",
                            "tool_result" when IsNoiseAgentDetail(detail) => $"✔ {CompactAgentResult(detail)}",
                            "tool_result" => $"✔ 工具结果 {detail}",
                            "artifact_ready" => $"产物就绪 {detail}",
                            "permission_request" => $"⚠ 权限请求 {detail}（当前写入策略可能已自动放行）",
                            _ => detail,
                        };
                        assistantMsg.AddThinkingStep(label);
                        // 同步进轨迹时间线（回看/实时共用）
                        var step = new DocMind.Models.AgentTrajectoryStep
                        {
                            Step = (assistantMsg.TrajectorySteps?.Count ?? 0) + 1,
                            Type = kind,
                            ToolId = detail,
                            Summary = detail,
                            FinalText = detail,
                        };
                        var list = assistantMsg.TrajectorySteps is { } existing
                            ? new System.Collections.Generic.List<DocMind.Models.AgentTrajectoryStep>(existing) { step }
                            : new System.Collections.Generic.List<DocMind.Models.AgentTrajectoryStep> { step };
                        assistantMsg.TrajectorySteps = list;
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyAgent);
                    }
                    else
                    {
                        ApplyAgent();
                    }
                },
                                onPermissionRequest: (requestId, toolId) =>
                {
                    void ApplyPerm()
                    {
                        PendingPermissionRequestId = requestId;
                        PendingPermissionToolId = string.IsNullOrWhiteSpace(toolId) ? "工作区写入" : toolId;
                        HasPendingPermission = !string.IsNullOrWhiteSpace(requestId);
                        assistantMsg.AddThinkingStep($"⚠ 等待授权：{PendingPermissionToolId}");
                    }
                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                        dispatcher.InvokeAsync(ApplyPerm);
                    else
                        ApplyPerm();
                },onThinking: thinking =>
                {
                    void ApplyThinking()
                    {
                        // 推理链增量累积到「思考过程」区（DeepSeek-R1/Qwen3 等）
                        assistantMsg.IsThinkingInProgress = true;
                        assistantMsg.UpdateLiveThinkingDuration(sw.ElapsedMilliseconds);
                        assistantMsg.AppendThinking(thinking ?? "");
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyThinking);
                    }
                    else
                    {
                        ApplyThinking();
                    }
                },
                onRestart: () =>
                {
                    void ApplyRestart()
                    {
                        // 后端精简上下文后重新生成：上一次尝试的正文是废弃半成品，
                        // 必须显式丢弃，否则新旧两次尝试会首尾相接变成重复内容
                        firstTokenApplied = false;
                        assistantMsg.Content = string.Empty;
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyRestart);
                    }
                    else
                    {
                        ApplyRestart();
                    }
                },
                onDone: result =>
                {
                    final = result;
                    try { timerCts.Cancel(); } catch { }
                    void ApplyDone()
                    {
                        assistantMsg.IsLoading = false;
                        OnPropertyChanged(nameof(HtmlDisplaySource));
                        OnPropertyChanged(nameof(IsHtmlStreaming));
                        OnPropertyChanged(nameof(HasHtmlMessage));
                        assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
                        // 优先用后端 done 帧 model_spec 确认的显示名（而非本地预测的模型 ID）
                        assistantMsg.Model = string.IsNullOrEmpty(result.ModelDisplayName)
                            ? result.Model
                            : result.ModelDisplayName;
                        assistantMsg.Provider = result.Provider;
                        assistantMsg.ElapsedMs = result.ElapsedMs;
                        // P0：截断/续写可见
                        // 注意：不要把「可继续写」提示拼进 Content——续写会把模型续文
                        // 接在正文后，而后端历史合并时不包含该提示，会导致 UI 与会话历史不一致。
                        assistantMsg.Truncated = result.Truncated;
                        assistantMsg.Partial = result.Partial;
                        assistantMsg.PromptTrack = result.PromptTrack;
                        assistantMsg.TruncatedHint = result.Truncated
                            ? (string.IsNullOrWhiteSpace(result.ContinueHint)
                                ? "回答可能被输出上限截断，可点击「继续写」补全"
                                : result.ContinueHint)
                            : null;
                        assistantMsg.ShowContinueWriting = result.Truncated
                            || (result.Partial && result.ContinueSupported && !string.IsNullOrEmpty(assistantMsg.Content));
                        // 续写 done 帧 sources 为空：不得清空调用前已有的引用列表
                        if (!(continueWriting && (result.Sources is null || result.Sources.Count == 0)))
                        {
                            assistantMsg.Sources = result.Sources;
                        }
                        if (result.Evidence is not null)
                        {
                            assistantMsg.Evidence = result.Evidence;
                        }
                        if (result.Truncated)
                        {
                            StatusMessage = string.IsNullOrWhiteSpace(result.ContinueHint)
                                ? "⚠ 回答可能被输出上限截断，可点击「继续写」补全"
                                : $"⚠ {result.ContinueHint}";
                        }
                        // 终帧后强制重新解析 Markdown,确保最终渲染完整(不受流式节流影响)
                        assistantMsg.ForceRefreshRender();
                        // 后端自动路由的创作意图会回传实际生效人设；仅当其为创作模式且与当前
                        // 不同时，静默同步人设下拉框（不重发请求），保证下一句请求与 UI 状态一致。
                        if (!string.IsNullOrEmpty(result.Persona)
                            && CreativePersonaIds.Contains(result.Persona!)
                            && result.Persona != _selectedPersona?.Id)
                        {
                            var persona = AvailablePersonas.FirstOrDefault(x => x.Id == result.Persona);
                            if (persona is not null)
                            {
                                _selectedPersona = persona;
                                OnPropertyChanged(nameof(SelectedPersona));
                            }
                        }
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyDone);
                    }
                    else
                    {
                        ApplyDone();
                    }
                },
                ct: cts.Token);

            sw.Stop();
            DebugLog.Info(
                $"流式统计: tokens={tokenCount} 首token={(firstTokenMs >= 0 ? $"{firstTokenMs}ms" : "未收到")} " +
                $"收到done帧={(final is not null ? "是" : "否")} 总耗时{sw.ElapsedMilliseconds}ms",
                "Chat");

            // 终帧：回写多轮 chat_id + 元数据（消息属性已由 onDone 回调在 UI 线程写入，
            // 此处不再重复赋值 Sources/Model/Provider/ElapsedMs）
            if (final is not null)
            {
                var isNewChat = _chatId is null && !string.IsNullOrEmpty(final.ChatId);
                _chatId = final.ChatId ?? _chatId;
                // 多轮：无 done 帧时后端可能已生成 chat_id，但前端不知道；
                // 日志便于排查「第二问失忆」。
                if (string.IsNullOrEmpty(_chatId))
                {
                    DebugLog.Warn("本轮未获得 chat_id，下一问将开新会话（多轮上下文会断）", "Chat");
                }
                // 状态统计：token 数（流式帧计数）+ 思考耗时文案
                assistantMsg.TokenCount = tokenCount;
                if (final.ElapsedMs > 0)
                {
                    assistantMsg.ThinkingDurationText = $"用时 {final.ElapsedMs / 1000.0:F1} 秒";
                }

                StatusMessage = continueWriting
                    ? $"已续写 · 模型 {final.Model} · 耗时 {final.ElapsedMs}ms"
                    : $"模型: {final.Model} ({final.Provider}) · 引用 {final.TotalChunks} 块 · 耗时 {final.ElapsedMs}ms";

                // ── Phase 1: 异步提取记忆（不阻断 UI） ──
                // partial / 续写轮不提取：续写 query 是「继续写」而非事实问题，
                // 且 partial 内容不可靠，不得进入跨会话记忆。
                if (!continueWriting && !final.Partial && !string.IsNullOrWhiteSpace(assistantMsg.Content))
                {
                    _ = Task.Run(async () => await ExtractAndStoreMemoryAsync(query, assistantMsg.Content));
                }

                // ── Phase 3: 异步记录费用（不阻断 UI） ──
                if (_costTracker is { } tracker && !string.IsNullOrEmpty(final.Model))
                {
                    _ = Task.Run(async () =>
                    {
                        try
                        {
                            // 后端未返回 token 数时用帧计数估算
                            var promptEstimate = query.Length / 4; // 粗估 prompt tokens
                            await tracker.LogCallAsync(
                                final.Model, final.Provider ?? "",
                                promptEstimate, tokenCount,
                                chatId: final.ChatId);
                        }
                        catch (Exception ex)
                        {
                            DebugLog.Warn($"费用记录失败（不阻断对话）: {ex.Message}", "Cost");
                        }
                    });
                }
                DebugLog.Info($"对话完成(流式): elapsed={final.ElapsedMs}ms model={final.Model} chunks={final.TotalChunks} sources={final.Sources.Count} chatId='{final.ChatId}' partial={final.Partial}", "Chat");

                // 新会话首条回答完成 → 刷新会话列表（标题/条数已生成），选中当前会话
                if (isNewChat)
                {
                    _ = LoadSessionsAsync(selectChatId: _chatId);
                }
            }
            else
            {
                DebugLog.Warn("未收到 done 终帧：多轮 chat_id 未更新、来源/模型元数据缺失（后端可能异常中断流）", "Chat");
                StatusMessage = "回答生成失败 · 连接中断";
                // 流被对端关闭但没给终帧：同样收尾思考链路，避免「思考中/正在生成…」悬挂
                assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
                assistantMsg.FailThinkingStep("连接中断，未收到完成帧");
                // 流中断时恢复输入框，避免用户丢失原始问题
                if (tokenCount > 0)
                {
                    // 已收到部分 token 但终帧丢失：保留已生成内容，追加重试引导
                    assistantMsg.Content += $"\n\n> ⚠️ 回答因连接中断未完成，以上为已生成的部分内容。";
                    assistantMsg.Content += $"\n> 可点击「重新生成」获取完整回答。";
                }
                else
                {
                    assistantMsg.Content = "❌ 回答生成失败：与模型服务的连接中断，未收到完成帧。";
                    assistantMsg.Content += $"\n\n💡 可能原因：";
                    assistantMsg.Content += $"\n1. 模型服务暂时不可用或响应超时";
                    assistantMsg.Content += $"\n2. 网络连接不稳定";
                    assistantMsg.Content += $"\n3. 上下文过长（多个知识库 + 联网搜索结果拼接）导致超时";
                    assistantMsg.Content += $"\n\n建议：点击「重新生成」重试，或减少勾选的知识库数量后重试。";
                }
                // 恢复原始问题到输入框，方便用户修改后重试
                InputText = query;
            }
            assistantMsg.IsLoading = false;
            // 后端声明的部分回答（网络中断/用户停止等）：追加警示说明，与正常完成区分。
            // 截断（Truncated）的 warning 不写入 Content：续写合并时后端历史不含该行，
            // 否则 UI 正文与会话库不一致；截断提示走 TruncatedHint + 状态栏。
            if (final is not null && final.Partial && !final.Truncated && !string.IsNullOrEmpty(final.Warning))
            {
                assistantMsg.Content += $"\n\n> ⚠️ {final.Warning}";
            }
            UpdateMessageFlags();
        }
        catch (OperationCanceledException)
        {
            sw.Stop();
            StatusMessage = "已停止生成";
            DebugLog.Info("对话已停止", "Chat");
            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
            assistantMsg.FailThinkingStep(null, "已停止生成");
            if (string.IsNullOrEmpty(assistantMsg.Content))
            {
                // 空正文时带上最后阶段，避免用户只看到「已停止」不知卡在哪
                var last = assistantMsg.ThinkingSteps.LastOrDefault() ?? "";
                var stage = last.Contains("规划")
                    ? "卡在回答规划（LLM 未返回）"
                    : last.Contains("联网") || last.Contains("搜索")
                        ? "卡在联网检索"
                        : last.Contains("检索")
                            ? "卡在知识库检索"
                            : last.Contains("生成")
                                ? "卡在正文生成（模型未出 token）"
                                : "尚未进入正文生成";
                assistantMsg.Content = $"（已停止生成 · {stage} · 用时 {sw.Elapsed.TotalSeconds:0.#}s）\n\n" +
                    "💡 慢模型规划/深度联网可能超过 1–3 分钟。可：改「知识库 RAG」+关深度搜索先拿短答，或换更快模型后重试。";
            }
        }
        catch (ApiException ex)
        {
            sw.Stop();
            var hint = LlmConfigHint(ex.Message);
            StatusMessage = hint is null
                ? $"API 错误：{ex.Message}"
                : $"API 错误：{ex.Message}（请到设置页检查 LLM 配置）";
            DebugLog.Error($"对话 API 错误: code={ex.Code} message={ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.IsWaitingForFirstToken = false;
            // 收尾思考链路：锁定耗时、关闭阶段胶囊、末尾「正在…」改为失败标记，
            // 避免错误提示旁仍显示「正在生成回答…」
            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
            assistantMsg.FailThinkingStep(ex.Message);
            if (string.IsNullOrWhiteSpace(assistantMsg.Content))
            {
                // 首字都没收到：整条替换为错误提示
                assistantMsg.Content = hint is null
                    ? $"❌ API 错误：{ex.Message}"
                    : $"❌ {hint}";
            }
            else
            {
                // 流式中途断开（网络抖动/后端重试耗尽）：保留已生成的部分回答，
                // 追加简洁的中断说明，让用户能复制已得内容或点「重新生成」续写。
                assistantMsg.Content += $"\n\n> ⚠️ 回答中断：{ex.Message}\n> 已生成内容已保留，可点击「重新生成」继续。";
            }
            // 发送失败时，将原文回填到输入框，避免草稿丢失
            InputText = query;
        }
        catch (BackendConnectionException ex)
        {
            BackendUnreachable?.Invoke();
            sw.Stop();
            StatusMessage = "无法连接到后端";
            DebugLog.Error($"对话连接失败: {ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.IsWaitingForFirstToken = false;
            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
            assistantMsg.FailThinkingStep(ex.Message);
            assistantMsg.Content = $"❌ 无法连接到后端服务: {ex.Message}\n\n💡 请检查:\n1. 端口是否被占用\n2. 可尝试在【设置】中修改「后端连接地址」端口并保存";
            InputText = query;
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"对话未知异常: {ex.GetType().Name}: {ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.IsWaitingForFirstToken = false;
            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
            assistantMsg.FailThinkingStep(ex.Message);
            assistantMsg.Content = $"❌ 错误：{ex.Message}";
            InputText = query;
        }
        finally
        {
            timerCts.Cancel();
            timerCts.Dispose();
            _cts?.Dispose();
            _cts = null;
            IsBusy = false;
            ShowStopChanged();
            OnPropertyChanged(nameof(HasMessages));
            // 对话完成后智能推荐最佳角色与主题
            AnalyzeAndRecommend();
        }
    }

    private CancellationTokenSource? _cts;

    /// <summary>LLM 鉴权/未配置类错误 → 返回引导到设置页的提示文案；其余错误返回 null。
    /// 后端把 LLM 调用错误包在 RAG_ERROR 的消息文本里（如「API Key 无效 (HTTP 401)」「未选择 LLM 提供商」），
    /// 这里按关键词启发式分类，给用户可操作的下一步而不是裸错误。</summary>
    private static string? LlmConfigHint(string? message)
    {
        if (string.IsNullOrEmpty(message))
        {
            return null;
        }
        var m = message.ToLowerInvariant();
        var isAuth = m.Contains("401") || m.Contains("403") || m.Contains("unauthorized")
            || m.Contains("api key") || m.Contains("apikey") || m.Contains("鉴权") || m.Contains("密钥");
        var isNotConfigured = m.Contains("未配置") || m.Contains("未设置") || m.Contains("未选择")
            || m.Contains("llm_provider") || m.Contains("provider=none") || m.Contains("no provider");
        return (isAuth, isNotConfigured) switch
        {
            (true, _) => "API Key 无效或未配置：请到【设置 → 大模型对话】检查 API Key 后点「保存」",
            (_, true) => "尚未配置 LLM：请到【设置 → 大模型对话】选择提供商、填写 API Key，并点「测试连接」验证",
            _ => null,
        };
    }

    /// <summary>停止生成。</summary>
    [RelayCommand(CanExecute = nameof(CanStop))]
    private void Stop()
    {
        DebugLog.Info("请求停止生成（用户点击停止按钮）", "Chat");
        try
        {
            _cts?.Cancel();
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"取消生成时异常: {ex.Message}", "Chat");
        }
    }

    private void ShowStopChanged()
    {
        OnPropertyChanged(nameof(ShowStop));
        StopCommand.NotifyCanExecuteChanged();
    }

    /// <summary>清空当前对话视图（不删除后端会话记录）。</summary>
    [RelayCommand]
    private void Clear()
    {
        DebugLog.Info($"清空对话: messages={Messages.Count} chatId='{_chatId ?? "-"}'", "Chat");
        // 进行中则先停止
        try { _cts?.Cancel(); } catch { /* ignore */ }
        Messages.Clear();
        _chatId = null;
        _selectedSession = null;
        InputText = string.Empty;
        OnPropertyChanged(nameof(SelectedSession));
        StatusMessage = "就绪";
        OnPropertyChanged(nameof(HasMessages));
    }

    /// <summary>开始新会话（清空视图并取消会话选中）。</summary>
    [RelayCommand]
    private void NewChat() => Clear();

    /// <summary>删除历史会话（后端 SQLite + 内存）；删除当前会话则切到新会话。</summary>
    [RelayCommand]
    private async Task DeleteSessionAsync(ChatSessionItem? session)
    {
        session ??= SelectedSession;
        if (session is null || _isLoadingSessions)
            return;

        try
        {
            await _apiService.DeleteChatAsync(session.ChatId);
            Sessions.Remove(session);
            DebugLog.Info($"已删除会话: {session.ChatId} '{session.Title}'", "Chat");

            if (session.ChatId == _chatId || session.ChatId == SelectedSession?.ChatId)
            {
                Clear();
            }
            StatusMessage = $"已删除会话：{session.Title}";
        }
        catch (Exception ex)
        {
            StatusMessage = $"删除会话失败：{ex.Message}";
            DebugLog.Warn($"删除会话失败: {session.ChatId}: {ex.Message}", "Chat");
        }
    }

    /// <summary>撤回用户消息并回填到输入框。
    /// 支持任意位置的用户消息：移除该消息及其后续所有消息（含对应的 AI 回答），
    /// 回填到输入框供修改后重新发送。后续消息会作为新的轮次附加到现有对话中（保留多轮上下文）。</summary>
    [RelayCommand]
    private void Withdraw(ChatMessage? message = null)
    {
        if (IsBusy || Messages.Count == 0)
            return;

        int userIdx = -1;
        if (message != null)
        {
            userIdx = Messages.IndexOf(message);
        }
        else
        {
            // 默认撤回最后一条用户消息
            for (var i = Messages.Count - 1; i >= 0; i--)
            {
                if (Messages[i].Role == "user")
                {
                    userIdx = i;
                    break;
                }
            }
        }

        if (userIdx < 0 || userIdx >= Messages.Count || Messages[userIdx].Role != "user")
            return;

        var content = Messages[userIdx].Content;
        var removedCount = Messages.Count - userIdx;
        // 移除该用户消息及其后的所有消息（如对应的 AI 回答）
        while (Messages.Count > userIdx)
        {
            Messages.RemoveAt(Messages.Count - 1);
        }

        InputText = content;
        StatusMessage = removedCount > 2
            ? $"已撤回第 {userIdx / 2 + 1} 轮对话（含 {removedCount - 1} 条后续消息）并回填至输入框"
            : "已撤回消息并回填至输入框";
        DebugLog.Info($"已撤回用户消息: idx={userIdx} removed={removedCount} content='{(content.Length > 50 ? content[..50] + "…" : content)}'", "Chat");
        UpdateMessageFlags();
    }

    /// <summary>维护消息级标志（复制由 CanCopy 自算；重新生成仅最后一条 assistant 显示；撤回仅最后一条 user 消息在非忙碌时显示）。</summary>
    private void UpdateMessageFlags()
    {
        int lastUserIdx = -1;
        for (var i = Messages.Count - 1; i >= 0; i--)
        {
            if (Messages[i].Role == "user")
            {
                lastUserIdx = i;
                break;
            }
        }

        for (var i = 0; i < Messages.Count; i++)
        {
            var m = Messages[i];
            m.ShowRegenerate = i == Messages.Count - 1 && m.Role == "assistant" && !IsBusy;
            m.ShowWithdraw = i == lastUserIdx && !IsBusy;
            var isLastAssistant = i == Messages.Count - 1 && m.Role == "assistant";
            m.ShowContinueWriting = !IsBusy && isLastAssistant && (m.Truncated || m.Partial);
        }
        ContinueWritingCommand.NotifyCanExecuteChanged();
        RegenerateCommand.NotifyCanExecuteChanged();
    }

    /// <summary>拉取历史会话列表（后端不可达时静默）。selectChatId 非空时选中该会话；
    /// search 非空时走后端全库过滤（标题/消息内容）。</summary>
    private async Task LoadSessionsAsync(string? selectChatId = null, string? search = null, CancellationToken cancellationToken = default)
    {
        try
        {
            var list = await _apiService.ListChatsAsync(limit: 50, q: search, ct: cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            _isLoadingSessions = true;
            try
            {
                Sessions.Clear();
                foreach (var s in list.Chats)
                {
                    Sessions.Add(new ChatSessionItem
                    {
                        ChatId = s.ChatId,
                        Title = s.Title,
                        MessageCount = s.MessageCount,
                        UpdatedAt = s.UpdatedAt,
                    });
                }
                OnPropertyChanged(nameof(FilteredSessions));

                // 选中目标会话：优先 selectChatId（新完成的首答），否则跟随当前 chatId
                var targetId = selectChatId ?? _chatId;
                SelectedSession = targetId is null
                    ? null
                    : Sessions.FirstOrDefault(s => s.ChatId == targetId);
            }
            finally
            {
                _isLoadingSessions = false;
            }
            DebugLog.Info($"会话列表加载完成: {Sessions.Count} 个 q={search ?? "-"}", "Chat");
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"加载会话列表失败: {ex.Message}", "Chat");
        }
    }

    /// <summary>选中历史会话 → 拉取全部消息载入视图，续聊沿用同一 chatId（后端从 DB 恢复上下文）。</summary>
    private async Task LoadSessionMessagesAsync(ChatSessionItem session)
    {
        try
        {
            var detail = await _apiService.GetChatAsync(session.ChatId);
            Messages.Clear();
            foreach (var m in detail.Messages)
            {
                var msg = new ChatMessage
                {
                    Role = m.Role,
                    Content = m.Content,
                    Sources = m.Sources,
                    TrajectorySteps = m.Trajectory,
                };
                // 渲染模式探测（选项 A，零后端改动）：后端会话体不存 render_mode，
                // 按内容判——含 ```html 围栏或 <!DOCTYPE/<html 开头 → HTML 体验消息
                if (m.Role == "assistant" && Controls.HtmlAnswerBubble.LooksLikeHtml(m.Content))
                {
                    msg.AnswerFormat = "html";
                }
                // 历史消息正文里的 [n] 角标同样可点击打开来源
                msg.SourceMarkerRequested += index =>
                {
                    var src = msg.Sources?.FirstOrDefault(s => s.Index == index);
                    if (src is not null)
                    {
                        OpenSource(src);
                    }
                };
                AttachFeedbackSink(msg);
                Messages.Add(msg);
            }
            _chatId = session.ChatId;
            StatusMessage = $"已载入会话：{session.Title}（{detail.Messages.Count} 条消息，可继续追问）";
            DebugLog.Info($"载入历史会话: {session.ChatId} messages={detail.Messages.Count}", "Chat");
        }
        catch (Exception ex)
        {
            StatusMessage = $"载入会话失败：{ex.Message}";
            DebugLog.Error($"载入历史会话失败: {session.ChatId}: {ex.Message}", "Chat", ex);
        }
    }

/// <summary>一键体验官方示例文档库（针对新手/空状态）。</summary>
    [RelayCommand]
    private async Task IngestSampleKnowledgeAsync()
    {
        if (IsBusy) return;

        IsBusy = true;
        StatusMessage = "正在导入新手官方示例知识库...";
        try
        {
            var res = await _apiService.IngestSampleAsync("default");
            if (res.Ok)
            {
                await LoadCollectionsAsync();
                var def = Collections.FirstOrDefault(c => c.Name == "default");
                if (def != null && !def.IsSelected)
                {
                    def.IsSelected = true;
                }
                InputText = "请总结 DocMind 的核心能力与支持的文档格式";
                StatusMessage = $"示例知识库导入成功！(分块: {res.ChunkCount})，已为您准备好体验问题";
                _notifications?.Success("官方示例文档已导入！快来体验智能问答吧", "极速体验");
            }
            else
            {
                StatusMessage = $"导入失败: {res.Error ?? "未知错误"}";
                _notifications?.Error($"示例库导入失败: {res.Error}");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"导入异常: {ex.Message}";
            DebugLog.Error($"导入示例文档异常: {ex.Message}", "Chat", ex);
        }
        finally
        {
            IsBusy = false;
        }
    }

    /// <summary>点击新手快捷提问芯片直接发送问题。</summary>
    [RelayCommand]
    private async Task QuickAskAsync(string? question)
    {
        if (string.IsNullOrWhiteSpace(question) || IsBusy) return;
        InputText = question.Trim();
        await SendAsync();
    }

    /// <summary>导出当前对话记录为 Markdown 文件或复制到剪贴板。</summary>
    [RelayCommand]
    private void ExportChat()
    {
        if (Messages.Count == 0)
        {
            _notifications?.Warning("当前没有对话记录可导出");
            return;
        }

        var sb = new System.Text.StringBuilder();
        sb.AppendLine($"# DocMind 对话记录导出");
        sb.AppendLine($"- **导出时间**：{DateTime.Now:yyyy-MM-dd HH:mm:ss}");
        sb.AppendLine($"- **会话 ID**：{_chatId ?? "临时会话"}");
        sb.AppendLine($"- **使用模型**：{EffectiveModel}");
        sb.AppendLine();
        sb.AppendLine("---");
        sb.AppendLine();

        int round = 1;
        foreach (var msg in Messages)
        {
            if (msg.Role == "user")
            {
                sb.AppendLine($"### 👤 用户 (第 {round} 轮)");
                sb.AppendLine(msg.Content);
                sb.AppendLine();
            }
            else if (msg.Role == "assistant")
            {
                sb.AppendLine($"### DocMind 文献引擎");
                sb.AppendLine(msg.Content);
                sb.AppendLine();
                if (msg.Sources != null && msg.Sources.Count > 0)
                {
                    sb.AppendLine("**📚 引用参考资料：**");
                    foreach (var s in msg.Sources)
                    {
                        sb.AppendLine($"- [{s.Index}] `{s.Source}` ({s.ScoreBadgeText})");
                    }
                    sb.AppendLine();
                }
                sb.AppendLine("---");
                sb.AppendLine();
                round++;
            }
        }

        var markdownText = sb.ToString();

        try
        {
            var saveFileDialog = new Microsoft.Win32.SaveFileDialog
            {
                Title = "导出对话记录",
                Filter = "Markdown 文件 (*.md)|*.md|文本文件 (*.txt)|*.txt|所有文件 (*.*)|*.*",
                FileName = $"DocMind_Chat_{DateTime.Now:yyyyMMdd_HHmmss}.md",
                DefaultExt = ".md"
            };

            if (saveFileDialog.ShowDialog() == true)
            {
                System.IO.File.WriteAllText(saveFileDialog.FileName, markdownText, System.Text.Encoding.UTF8);
                _notifications?.Success($"对话记录已成功导出至：{System.IO.Path.GetFileName(saveFileDialog.FileName)}");
                StatusMessage = $"已导出文件：{saveFileDialog.FileName}";
            }
            else
            {
                // 若用户取消保存对话框，则复制至剪贴板作为备选
                Clipboard.SetText(markdownText);
                _notifications?.Success("已将对话记录 (Markdown) 复制到剪贴板");
                StatusMessage = "对话记录已复制到剪贴板";
            }
        }
        catch (Exception ex)
        {
            DebugLog.Error($"导出对话记录异常: {ex.Message}", "Chat", ex);
            _notifications?.Error($"导出失败: {ex.Message}");
        }
    }

    /// <summary>会话含 HTML 体验消息时可用：把该消息的自包含整页 HTML 写为 .html 文件（可双击在浏览器打开）。</summary>
    public bool HasHtmlMessage => Messages.Any(m => m.IsHtmlAnswer);

    /// <summary>HTML 阅读区展示的整页 HTML（取最近一条 HTML 体验消息）。</summary>
    public string? HtmlDisplaySource =>
        Messages.LastOrDefault(m => m.IsHtmlAnswer)?.Content;

    /// <summary>HTML 阅读区是否仍在生成（转发到该条的 IsLoading）。</summary>
    public bool IsHtmlStreaming =>
        Messages.LastOrDefault(m => m.IsHtmlAnswer)?.IsLoading == true;

    [RelayCommand]
    private void ExportHtmlPage()
    {
        var htmlMsg = Messages.FirstOrDefault(m => m.IsHtmlAnswer);
        if (htmlMsg is null)
        {
            _notifications?.Warning("当前会话没有 HTML 体验消息可导出");
            return;
        }

        try
        {
            var page = Controls.HtmlAnswerBubble.StripHtmlFence(htmlMsg.Content);
            if (string.IsNullOrWhiteSpace(page))
            {
                _notifications?.Warning("HTML 内容为空，无法导出");
                return;
            }
            var saveFileDialog = new Microsoft.Win32.SaveFileDialog
            {
                Title = "导出 HTML 页面",
                Filter = "HTML 文件 (*.html)|*.html|所有文件 (*.*)|*.*",
                FileName = $"DocMind_Page_{DateTime.Now:yyyyMMdd_HHmmss}.html",
                DefaultExt = ".html",
            };
            if (saveFileDialog.ShowDialog() == true)
            {
                System.IO.File.WriteAllText(saveFileDialog.FileName, page, System.Text.Encoding.UTF8);
                _notifications?.Success($"HTML 页面已导出至：{System.IO.Path.GetFileName(saveFileDialog.FileName)}");
                StatusMessage = $"已导出文件：{saveFileDialog.FileName}";
            }
        }
        catch (Exception ex)
        {
            DebugLog.Error($"导出 HTML 页面异常: {ex.Message}", "Chat", ex);
            _notifications?.Error($"导出失败: {ex.Message}");
        }
    }

    /// <summary>在 Windows 文件资源管理器中定位当前引用的原文件。</summary>
    [RelayCommand]
    private void RevealSourceInExplorer()
    {
        if (SelectedSource is null || string.IsNullOrWhiteSpace(SelectedSource.Source)) return;

        var path = SelectedSource.Source;
        if (path.StartsWith("note:", StringComparison.OrdinalIgnoreCase))
        {
            _notifications?.Warning("该引用为即时沉淀笔记，非物理磁盘文件");
            return;
        }

        try
        {
            if (System.IO.File.Exists(path))
            {
                Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{path}\"") { UseShellExecute = true });
            }
            else if (System.IO.Directory.Exists(path))
            {
                Process.Start(new ProcessStartInfo("explorer.exe", $"\"{path}\"") { UseShellExecute = true });
            }
            else
            {
                _notifications?.Warning($"未在本地磁盘找到文件路径：{path}");
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"在资源管理器中定位失败: {ex.Message}", "Chat");
            _notifications?.Error($"定位文件失败: {ex.Message}");
        }
    }

    /// <summary>Agent 工具结果是否为低价值（无变化/无提取/跳过…），主时间线只留短标签。</summary>
    private static bool IsNoiseAgentDetail(string? detail)
    {
        if (string.IsNullOrWhiteSpace(detail))
        {
            return true;
        }
        string[] markers =
        {
            "无变化", "无提取", "无关联", "未变化", "无更新", "已跳过", "跳过",
            "映射不可用", "结果不可用", "强制完成", "完整性评分",
        };
        return markers.Any(m => detail.Contains(m, StringComparison.Ordinal));
    }

    /// <summary>把冗长工具结果收成 ≤16 字摘要；失败/降级信息保留在 Detail（由 ThinkingStep.Parse 承接）。</summary>
    private static string CompactAgentResult(string detail)
    {
        var d = detail.Trim();
        if (d.Contains("映射不可用", StringComparison.Ordinal))
        {
            return "LLM 映射不可用";
        }
        if (d.Contains("结果不可用", StringComparison.Ordinal))
        {
            return "LLM 结果不可用";
        }
        if (d.Contains("无变化", StringComparison.Ordinal) || d.Contains("无提取", StringComparison.Ordinal))
        {
            return "无变化";
        }
        if (d.Contains("跳过", StringComparison.Ordinal))
        {
            return "已跳过";
        }
        // 「toolId: summary」形态：只留 toolId + 截短 summary
        var cut = d.IndexOf(':');
        if (cut > 0 && cut < 12 && d.Length > cut + 1)
        {
            var tail = d[(cut + 1)..].Trim();
            return tail.Length <= 12 ? $"{d[..cut].Trim()}·{tail}" : $"{d[..cut].Trim()}·{tail[..12]}…";
        }
        return d.Length <= 16 ? d : d[..16] + "…";
    }

    /// <summary>使用系统默认应用程序打开引用的原文件。</summary>
    [RelayCommand]
    private void OpenSourceFile()
    {
        if (SelectedSource is null || string.IsNullOrWhiteSpace(SelectedSource.Source)) return;

        var path = SelectedSource.Source;
        if (path.StartsWith("note:", StringComparison.OrdinalIgnoreCase))
        {
            _notifications?.Warning("该引用为即时沉淀笔记，非物理磁盘文件");
            return;
        }

        try
        {
            if (System.IO.File.Exists(path))
            {
                Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
            }
            else
            {
                _notifications?.Warning($"本地文件不存在或已被移动：{path}");
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"打开文件失败: {ex.Message}", "Chat");
            _notifications?.Error($"打开文件失败: {ex.Message}");
        }
    }

    /// <summary>对话页拉取模型列表：默认提供商（已配 Key 时）+ 各已配置服务商档案实时拉取。
    /// 仅本会话显示用，不落盘；选中项由 RebuildModelChoices 保留。</summary>
    [RelayCommand]
    private async Task RefreshModelsAsync()
    {
        var fetchedProfiles = 0;
        var failedProfiles = 0;
        string? defaultError = null;
        try
        {
            // 1) 默认提供商（设置页全局配置）：仅在已配 Key 时拉取，否则跳过（provider=none 必失败）
            if (_defaultKeyConfigured)
            {
                try
                {
                    var result = await _apiService.LlmModelsAsync(new LlmModelsRequest { Timeout = 10 });
                    if (result.Ok)
                    {
                        _defaultProviderModels.Clear();
                        if (!string.IsNullOrWhiteSpace(_configuredModel))
                        {
                            _defaultProviderModels.Add(_configuredModel);
                        }
                        if (string.IsNullOrWhiteSpace(_appSettings.LastChatProfileId)
                            && !string.IsNullOrWhiteSpace(_appSettings.LastChatModel))
                        {
                            _defaultProviderModels.Add(_appSettings.LastChatModel.Trim());
                        }
                        foreach (var m in result.Models.Where(m => !string.IsNullOrWhiteSpace(m)))
                        {
                            _defaultProviderModels.Add(m);
                        }
                        if (!string.IsNullOrWhiteSpace(result.Provider) && result.Provider != _configuredProvider)
                        {
                            _configuredProvider = result.Provider;
                            OnPropertyChanged(nameof(EffectiveProvider));
                            OnPropertyChanged(nameof(EffectiveModelSummary));
                            OnPropertyChanged(nameof(IsLlmConfigured));
                            OnPropertyChanged(nameof(EmptyGuideText));
                        }
                        DebugLog.Info($"对话页默认提供商模型列表: provider={result.Provider} count={result.Models.Count}", "Chat");
                    }
                    else
                    {
                        defaultError = result.Error ?? "未知错误";
                        DebugLog.Warn($"对话页默认提供商获取模型失败: {defaultError}", "Chat");
                    }
                }
                catch (Exception ex)
                {
                    defaultError = ex.Message;
                    DebugLog.Warn($"对话页默认提供商获取模型异常: {ex.Message}", "Chat");
                }
            }

            // 2) 各启用且有 Key 的服务商档案：实时拉取该账号下真实可用模型，缓存到 _profileLiveModels
            if (_appSettings.LlmProfiles is { Count: > 0 })
            {
                foreach (var p in _appSettings.LlmProfiles.Where(p => p is not null && p.IsEnabled
                    && (string.Equals(p.Provider, "ollama", StringComparison.OrdinalIgnoreCase)
                        || !string.IsNullOrWhiteSpace(p.ApiKey))))
                {
                    try
                    {
                        var pres = await _apiService.LlmModelsAsync(new LlmModelsRequest
                        {
                            Provider = p.Provider,
                            ApiKey = p.ApiKey,
                            BaseUrl = p.BaseUrl,
                            Timeout = 10,
                        });
                        if (pres.Ok)
                        {
                            _profileLiveModels[p.Id] = pres.Models
                                .Where(m => !string.IsNullOrWhiteSpace(m))
                                .Select(m => m.Trim())
                                .Distinct(StringComparer.OrdinalIgnoreCase)
                                .ToList();
                            fetchedProfiles++;
                        }
                        else
                        {
                            failedProfiles++;
                            DebugLog.Warn($"档案 {p.Name} 拉取模型失败: {pres.Error}", "Chat");
                        }
                    }
                    catch (Exception ex)
                    {
                        failedProfiles++;
                        DebugLog.Warn($"档案 {p.Name} 拉取模型异常: {ex.Message}", "Chat");
                    }
                }
            }

            RebuildModelChoices();
            if (defaultError is not null)
            {
                StatusMessage = $"❌ 获取模型列表失败: {defaultError}"
                    + (failedProfiles > 0 ? $"（另 {failedProfiles} 个服务商失败）" : "");
            }
            else if (fetchedProfiles > 0)
            {
                StatusMessage = $"✅ 已刷新 {fetchedProfiles} 个服务商的模型列表" + (failedProfiles > 0 ? $"（{failedProfiles} 个失败）" : "");
            }
            else if (failedProfiles > 0)
            {
                StatusMessage = $"❌ {failedProfiles} 个服务商拉取模型失败，请检查 Key/网络";
            }
            else if (!_defaultKeyConfigured)
            {
                StatusMessage = "未配置任何可用的服务商（请到设置页配置 API Key）";
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"❌ 获取模型列表失败: {ex.Message}";
            DebugLog.Warn($"对话页获取模型列表异常: {ex.Message}", "Chat");
        }
    }

    /// <summary>种子默认提供商候选：从后端配置取 llm_provider/llm_model（未拉列表前至少能看到配置值）。
    /// 同步后统一重建候选：首项「默认 · xx」显示名与默认提供商分组都会随后端配置更新。</summary>
    private async Task SeedModelFromConfigAsync()
    {
        try
        {
            var cfg = await _apiService.GetConfigAsync();
            var model = cfg?.LlmModel;
            _configuredProvider = cfg?.LlmProvider ?? "none";
            _configuredModel = model ?? string.Empty;
            // 后端权威覆盖：后端已落盘的 key 是否就绪（比本地 LlmApiKey 更准：可能被后端配置文件改过）
            _defaultKeyConfigured = cfg?.LlmApiKeyConfigured ?? _defaultKeyConfigured;
            OnPropertyChanged(nameof(EffectiveProvider));
            OnPropertyChanged(nameof(EffectiveModel));
            OnPropertyChanged(nameof(EffectiveModelSummary));
            OnPropertyChanged(nameof(IsLlmConfigured));
            OnPropertyChanged(nameof(EmptyGuideText));
            if (!string.IsNullOrWhiteSpace(model)
                && !_defaultProviderModels.Any(m => string.Equals(m.Trim(), model.Trim(), StringComparison.OrdinalIgnoreCase)))
            {
                _defaultProviderModels.Add(model.Trim());
            }
            // 模型名可能变化（首项显示「默认 · xx」），统一重建候选（保留当前选中项）
            RebuildModelChoices();
        }
        catch (Exception ex)
        {
            DebugLog.Debug($"读取后端配置种子模型失败（忽略）: {ex.Message}", "Chat");
        }
    }

    /// <summary>后端恢复在线时由 MainViewModel 调用：补拉 v1/config 种子默认提供商模型，
    /// 覆盖构造时因令牌竞态 401 失败的初载（SeedModelFromConfigAsync 本身无重试），
    /// 否则整个会话 _configuredModel 为空，「默认 · xx」退回占位符、默认提供商分组缺模型。</summary>
    public Task RefreshModelSeedAsync() => SeedModelFromConfigAsync();

    /// <summary>测试用：等待会话列表加载（构造时 fire-and-forget 不可 await）。</summary>
    internal Task SessionsLoadedForTestAsync() => LoadSessionsAsync();

    /// <summary>刷新历史会话列表（后端从离线恢复在线时由 MainViewModel 调用）。
    /// 构造时的加载是 fire-and-forget，后端未就绪时会失败且无重试，必须在此补一次。</summary>
    public Task RefreshSessionsAsync() => LoadSessionsAsync();

    /// <summary>测试用：等待选中会话的消息加载完成。</summary>
    internal Task SessionLoadedForTestAsync()
        => SelectedSession is null ? Task.CompletedTask : LoadSessionMessagesAsync(SelectedSession);
}

/// <summary>对话页模型选择器选项：DisplayName 显示文本；Provider 非空 = 属于某自定义服务商的模型；两者皆空 = 「设置页默认」伪项。</summary>
public sealed record ModelChoice(string DisplayName, LlmProfile? Provider, string? Model)
{
    /// <summary>是否「设置页默认」伪项（用后端全局配置，请求不携带 providerConfig）。</summary>
    public bool IsDefault => Provider is null && Model is null;

    public override string ToString() => DisplayName;
}

/// <summary>待发送附件项。</summary>
public sealed record AttachmentItem(string FullPath, string FileName, string Icon, string FileSizeText);
