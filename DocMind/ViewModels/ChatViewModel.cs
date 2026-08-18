using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;
using Markdig;
using Markdig.Wpf;

namespace DocMind.ViewModels;

/// <summary>单条对话消息（用户或助手）。可变 class 以支持流式增量追加 token。</summary>
public sealed partial class ChatMessage : System.ComponentModel.INotifyPropertyChanged
{
    private string _role = string.Empty;
    private bool _isIngested;
    private bool _isIngesting;

    /// <summary>是否已沉淀入知识库。</summary>
    public bool IsIngested
    {
        get => _isIngested;
        set => SetField(ref _isIngested, value);
    }

    /// <summary>是否正在沉淀入库中。</summary>
    public bool IsIngesting
    {
        get => _isIngesting;
        set => SetField(ref _isIngesting, value);
    }
    private string _content = string.Empty;
    private FlowDocument? _renderedDocument;
    private long _lastRenderTicks;
    /// <summary>reparse 节流间隔(ms):流式期间避免每个 token 都重新解析 Markdown。</summary>
    private const long RenderThrottleMs = 50;
    private IReadOnlyList<SourceRef>? _sources;
    private string? _model;
    private string? _provider;
    private int? _elapsedMs;
    private bool _isLoading;
    private bool _isWaitingForFirstToken;
    private bool _showRegenerate;
    private bool _showWithdraw;
    private string _waitingHint = "🧠 正在检索知识库并思考回答...";

    /// <summary>角色：user / assistant / system。</summary>
    public string Role
    {
        get => _role;
        set
        {
            if (SetField(ref _role, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(IsUser)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(IsAssistant)));
            }
        }
    }

    /// <summary>消息内容。</summary>
    public string Content
    {
        get => _content;
        set
        {
            if (SetField(ref _content, value))
            {
                UpdateRenderedDocument();
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(CanCopy)));
            }
        }
    }

    /// <summary>助手回答经 Markdig 解析后的 FlowDocument;用户消息或解析失败时为 null。</summary>
    /// <remarks>UI 层据此渲染 Markdown(代码块/列表/表格/可点击链接);用户消息仍走纯 TextBlock。</remarks>
    public FlowDocument? RenderedDocument
    {
        get => _renderedDocument;
        private set => SetField(ref _renderedDocument, value);
    }

    /// <summary>强制重新解析 Markdown(终帧 onDone 后调用,确保最终渲染完整,不受节流影响)。</summary>
    public void ForceRefreshRender() => UpdateRenderedDocument(force: true);

    /// <summary>流式增量追加 token。</summary>
    public void AppendToken(string token)
    {
        Content += token;
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(CanCopy)));
    }

    private static readonly System.Text.RegularExpressions.Regex ActionRegex =
        new(@"\[ACTIONS:\s*(\[.*?\])\s*\]", System.Text.RegularExpressions.RegexOptions.Singleline | System.Text.RegularExpressions.RegexOptions.Compiled);

    /// <summary>AI 根据上下文预测的下一步行动建议列表。</summary>
    public ObservableCollection<string> FollowUpActions { get; } = new();

    /// <summary>是否有下一步行动建议。</summary>
    public bool HasFollowUpActions => FollowUpActions.Count > 0;

    /// <summary>把 Content 用 Markdig 解析为 FlowDocument。流式期间节流,终帧后强制刷新。</summary>
    private void UpdateRenderedDocument(bool force = false)
    {
        // 用户消息不渲染 Markdown(纯文本即可,避免 Markdown 语法误解析)
        if (IsUser || string.IsNullOrEmpty(Content))
        {
            if (_renderedDocument is not null)
            {
                RenderedDocument = null;
            }
            return;
        }
        // 节流:流式期间频繁 reparse 浪费 CPU,50ms 一次足够流畅
        var now = Environment.TickCount64;
        if (!force && (now - _lastRenderTicks) < RenderThrottleMs)
        {
            return;
        }
        _lastRenderTicks = now;
        try
        {
            var rawContent = Content;
            var cleanContent = rawContent;
            var match = ActionRegex.Match(rawContent);
            if (match.Success)
            {
                cleanContent = rawContent.Remove(match.Index, match.Length).TrimEnd();
                try
                {
                    var json = match.Groups[1].Value;
                    var list = System.Text.Json.JsonSerializer.Deserialize<List<string>>(json);
                    if (list != null && list.Count > 0)
                    {
                        if (FollowUpActions.Count != list.Count || !FollowUpActions.SequenceEqual(list))
                        {
                            FollowUpActions.Clear();
                            foreach (var item in list)
                            {
                                if (!string.IsNullOrWhiteSpace(item))
                                {
                                    FollowUpActions.Add(item.Trim());
                                }
                            }
                            PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasFollowUpActions)));
                        }
                    }
                }
                catch
                {
                    var items = System.Text.RegularExpressions.Regex.Matches(match.Groups[1].Value, "\"([^\"]+)\"");
                    if (items.Count > 0)
                    {
                        FollowUpActions.Clear();
                        foreach (System.Text.RegularExpressions.Match item in items)
                        {
                            FollowUpActions.Add(item.Groups[1].Value.Trim());
                        }
                        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasFollowUpActions)));
                    }
                }
            }
            else
            {
                var partialIdx = rawContent.LastIndexOf("[ACTIONS:", StringComparison.OrdinalIgnoreCase);
                if (partialIdx >= 0 && partialIdx > rawContent.Length - 120)
                {
                    cleanContent = rawContent[..partialIdx].TrimEnd();
                }
            }

            var pipeline = new MarkdownPipelineBuilder().UseSupportedExtensions().Build();
            var doc = Markdig.Wpf.Markdown.ToFlowDocument(cleanContent, pipeline);
            // 对齐 ChatView 气泡样式:清除 FlowDocument 默认页边距,继承 BodyText 样式(FontSize=15, Medium, TextPrimary)
            doc.PagePadding = new Thickness(0);
            doc.FontFamily = SystemFonts.MessageFontFamily;
            doc.FontSize = 15;
            doc.FontWeight = FontWeights.Medium;
            doc.Foreground = (Brush)(System.Windows.Application.Current?.FindResource("TextPrimaryBrush")
                                     ?? Brushes.Black);
            RenderedDocument = doc;
        }
        catch (Exception ex)
        {
            // 解析失败:清空 RenderedDocument,UI 会 fallback 到纯 TextBlock(由 Visibility 控制)
            DebugLog.Warn($"Markdown 解析失败,降级纯文本: {ex.Message}", "Chat");
            if (_renderedDocument is not null)
            {
                RenderedDocument = null;
            }
        }
    }

    /// <summary>引用来源（仅 assistant 有）。</summary>
    public IReadOnlyList<SourceRef>? Sources
    {
        get => _sources;
        set => SetField(ref _sources, value);
    }

    /// <summary>模型名（仅 assistant 有）。</summary>
    public string? Model
    {
        get => _model;
        set => SetField(ref _model, value);
    }

    /// <summary>提供商标识（仅 assistant 有）。</summary>
    public string? Provider
    {
        get => _provider;
        set => SetField(ref _provider, value);
    }

    /// <summary>耗时 ms（仅 assistant 有）。</summary>
    public int? ElapsedMs
    {
        get => _elapsedMs;
        set => SetField(ref _elapsedMs, value);
    }

    /// <summary>是否正在加载。整体生成期间为 true。</summary>
    public bool IsLoading
    {
        get => _isLoading;
        set
        {
            if (SetField(ref _isLoading, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(CanCopy)));
            }
        }
    }

    /// <summary>是否正在等待首个 token返回（控制骨架屏可见性）。</summary>
    public bool IsWaitingForFirstToken
    {
        get => _isWaitingForFirstToken;
        set => SetField(ref _isWaitingForFirstToken, value);
    }

    /// <summary>是否显示「重新生成」（仅最后一条 assistant 消息、非生成中；由 VM 维护）。</summary>
    public bool ShowRegenerate
    {
        get => _showRegenerate;
        set => SetField(ref _showRegenerate, value);
    }

    /// <summary>是否显示「撤回」按钮（仅最后一条用户消息在非生成中显示）。</summary>
    public bool ShowWithdraw
    {
        get => _showWithdraw;
        set => SetField(ref _showWithdraw, value);
    }

    /// <summary>等待首字或非流式大包期间的动态状态提示文案。</summary>
    public string WaitingHint
    {
        get => _waitingHint;
        set => SetField(ref _waitingHint, value);
    }

    /// <summary>是否有可复制内容（控制「复制」按钮可见性）。</summary>
    public bool CanCopy => !IsLoading && !string.IsNullOrEmpty(Content);

    /// <summary>复制消息内容到剪贴板。</summary>
    [RelayCommand]
    private void Copy()
    {
        if (string.IsNullOrEmpty(Content))
        {
            return;
        }
        try
        {
            Clipboard.SetText(Content);
        }
        catch (Exception ex)
        {
            // 剪贴板被其他进程占用（OCR/远程桌面场景偶发）：重试一次，仍失败仅记录
            DebugLog.Warn($"复制到剪贴板失败: {ex.Message}", "Chat");
            try { Clipboard.SetText(Content); } catch { /* 放弃，不打断 UI */ }
        }
    }

    /// <summary>是否有引用来源（控制来源列表可见性）。</summary>
    public bool HasSources => Sources is { Count: > 0 };

    /// <summary>是否有模型信息（仅在 assistant 回答中显示）。</summary>
    public bool HasModel => Model is not null;

    /// <summary>是否来自用户（UI 分左右用）。</summary>
    public bool IsUser => Role == "user";

    /// <summary>是否来自助手（UI 渲染助手 Markdown 消息卡片用）。</summary>
    public bool IsAssistant => Role == "assistant";

    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;

    private bool SetField<T>(ref T field, T value, [System.Runtime.CompilerServices.CallerMemberName] string? propertyName = null)
    {
        if (!System.Collections.Generic.EqualityComparer<T>.Default.Equals(field, value))
        {
            field = value;
            PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(propertyName));
            return true;
        }
        return false;
    }
}

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

/// <summary>办公角色人设选项。</summary>
public sealed record PersonaOption(string Id, string DisplayName, string Icon, string Description)
{
    public override string ToString() => DisplayName;
}

/// <summary>历史会话列表项（ComboBox 显示用）。</summary>
public sealed class ChatSessionItem
{
    public string ChatId { get; init; } = string.Empty;

    /// <summary>会话标题（首条用户问题前 50 字）。</summary>
    public string Title { get; init; } = string.Empty;

    public int MessageCount { get; init; }

    /// <summary>最后更新时间（ISO，来自后端）。</summary>
    public string UpdatedAt { get; init; } = string.Empty;

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
    private readonly IDoc2kbApiService _apiService;
    private readonly NotificationService? _notifications;

    /// <summary>用户点击引用来源时的请求事件。MainViewModel 订阅后导航到搜索页并传入源文件名。</summary>
    public event Action<SourceRef>? SourceSearchRequested;

    private string _inputText = string.Empty;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private string? _chatId;
    private bool _isLoadingSessions;

    /// <summary>模型下拉首项伪值：表示「用设置页配置的默认模型」。</summary>
    public const string DefaultModelLabel = "默认（设置页模型）";

    private string _selectedModel = DefaultModelLabel;

    /// <summary>可选的办公角色人设列表。</summary>
    public IReadOnlyList<PersonaOption> AvailablePersonas { get; } = new List<PersonaOption>
    {
        new("office", "💼 知识办公助手", "💼", "提炼核心结论、梳理 Action Items 待办清单与标准公文润色"),
        new("architect", "🧠 资深系统架构师", "🧠", "系统设计模式选型、底层运行机制剖析、性能瓶颈评估与架构演进设计"),
        new("engineer", "🛠️ 资深研发工匠", "🛠️", "工业级代码实现、重构优化、异常边界防御与单元测试建议"),
        new("brainstorm", "💡 创新方案顾问", "💡", "头脑风暴、SWOT 矩阵分析、多方案多维度对比表格与排期落地规划"),
    };

    private PersonaOption _selectedPersona;

    /// <summary>当前选中的办公角色人设。</summary>
    public PersonaOption SelectedPersona
    {
        get => _selectedPersona;
        set
        {
            if (SetProperty(ref _selectedPersona, value ?? AvailablePersonas[0]))
            {
                StatusMessage = $"角色: {_selectedPersona.DisplayName}";
            }
        }
    }

    public ChatViewModel(IDoc2kbApiService apiService, NotificationService? notifications = null)
    {
        _apiService = apiService;
        _notifications = notifications;
        Title = "对话";
        _selectedPersona = AvailablePersonas[0];

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
            SendCommand.NotifyCanExecuteChanged();
        };
        LoadCollectionsCommand = new AsyncRelayCommand(LoadCollectionsAsync);
        AddCollectionCommand = new AsyncRelayCommand<string?>(AddCollectionAsync);

        Sessions = new ObservableCollection<ChatSessionItem>();
        AvailableModels = new ObservableCollection<string> { DefaultModelLabel };

        Messages.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(ShowEmptyGuide));
            OnPropertyChanged(nameof(EmptyGuideText));
            UpdateMessageFlags();
        };

        // 构造时拉取已有知识库集合 + 历史会话 + 后端当前模型（模型下拉种子）
        _ = LoadCollectionsAsync();
        _ = LoadSessionsAsync();
        _ = SeedModelFromConfigAsync();
    }

    /// <summary>可勾选的知识库集合列表（复选框）。</summary>
    public ObservableCollection<CollectionItem> Collections { get; }

    /// <summary>历史会话列表（持久化在后端 SQLite，重启可恢复）。</summary>
    public ObservableCollection<ChatSessionItem> Sessions { get; }

    /// <summary>模型下拉候选（首项为「默认」伪值，其余为拉取/种子模型）。</summary>
    public ObservableCollection<string> AvailableModels { get; }

    /// <summary>当前选中模型；DefaultModelLabel = 用设置页配置（请求不带 model）。</summary>
    public string SelectedModel
    {
        get => _selectedModel;
        set
        {
            if (SetProperty(ref _selectedModel, string.IsNullOrWhiteSpace(value) ? DefaultModelLabel : value))
            {
                StatusMessage = value == DefaultModelLabel ? "就绪" : $"模型: {value}（下条消息生效）";
            }
        }
    }

    private ChatSessionItem? _selectedSession;

    /// <summary>当前会话；null = 新会话（未发送过消息）。选中即载入历史消息并可续聊。</summary>
    public ChatSessionItem? SelectedSession
    {
        get => _selectedSession;
        set
        {
            if (SetProperty(ref _selectedSession, value) && value is not null && !_isLoadingSessions)
            {
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

    /// <summary>是否有输入内容。</summary>
    public bool HasInput => !string.IsNullOrWhiteSpace(InputText);

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
            SendCommand.NotifyCanExecuteChanged();
        }
    }

    private async Task LoadCollectionsAsync()
    {
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

            Collections.Clear();
            var list = names.ToList();
            list.Sort(StringComparer.OrdinalIgnoreCase);
            foreach (var name in list)
            {
                // 已有勾选则恢复；无任何勾选时（首次加载）默认选 default
                var isSelected = selectedNames.Contains(name)
                    || (selectedNames.Count == 0 && string.Equals(name, "default", StringComparison.OrdinalIgnoreCase));
                Collections.Add(new CollectionItem { Name = name, IsSelected = isSelected });
            }

            OnPropertyChanged(nameof(HasSelectedCollection));
            SendCommand.NotifyCanExecuteChanged();
            DebugLog.Info($"集合列表加载完成: {list.Count} 个 [{string.Join(", ", list)}]", "Chat");
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
    }

    private async Task AddCollectionAsync(string? name)
    {
        var trimmed = (name ?? NewCollectionName).Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return;
        }

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

        NewCollectionName = string.Empty;
        OnPropertyChanged(nameof(HasSelectedCollection));
        SendCommand.NotifyCanExecuteChanged();
    }

    /// <summary>对话消息列表。</summary>
    public ObservableCollection<ChatMessage> Messages { get; } = [];

    /// <summary>是否有消息。</summary>
    public bool HasMessages => Messages.Count > 0;

    /// <summary>无消息时显示空态。</summary>
    public bool ShowEmptyGuide => !IsBusy && Messages.Count == 0;

    /// <summary>空态引导文案。</summary>
    public string EmptyGuideText =>
        "开始与知识库对话。\n\n"
        + "DocMind 会检索已导入的文档，\n"
        + "结合多轮上下文生成带来源标注的回答。\n\n"
        + "还没导入文档？先到【导入】页添加文件。\n\n"
        + "💡 需要先配置 LLM：到【设置 → 大模型对话】\n"
        + "  选择提供商（OpenAI 兼容 / Claude / Gemini / Ollama）\n"
        + "  填写 API Key 后点「测试连接」验证";

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
            return;

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

    /// <summary>一键将回答沉淀为知识笔记入库（反哺私域知识库）。</summary>
    [RelayCommand]
    private async Task IngestMessageAsync(ChatMessage? message)
    {
        if (message == null || string.IsNullOrWhiteSpace(message.Content) || message.IsIngested || message.IsIngesting)
            return;

        message.IsIngesting = true;
        StatusMessage = "正在沉淀入库…";

        try
        {
            // 提取纯文本正文并去除标签
            var text = message.Content.Trim();
            var actionIdx = text.IndexOf("[ACTIONS:", StringComparison.OrdinalIgnoreCase);
            if (actionIdx >= 0)
            {
                text = text[..actionIdx].Trim();
            }

            // 自动提取标题（取首行前 30 字）
            var firstLine = text.Split('\n', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? text;
            firstLine = System.Text.RegularExpressions.Regex.Replace(firstLine, @"^[#\s\-*📌💡]+", "").Trim();
            var title = firstLine.Length > 30 ? firstLine[..30] + "…" : firstLine;
            if (string.IsNullOrWhiteSpace(title))
            {
                title = $"知识探讨沉淀 ({DateTime.Now:MM-dd HH:mm})";
            }

            var targetCollection = SelectedCollections.FirstOrDefault();

            var req = new IngestTextRequest
            {
                Text = text,
                Title = $"💡 {title}",
                Collection = targetCollection
            };

            await _apiService.IngestTextAsync(req);
            message.IsIngested = true;
            StatusMessage = $"已沉淀入库：{title}";
            DebugLog.Info($"对话内容沉淀入库成功: title={title}, collection={targetCollection ?? "auto"}", "Chat");

            // 异步刷新知识库集合
            _ = LoadCollectionsAsync();
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"沉淀入库失败: {ex.Message}", "Chat");
            StatusMessage = $"沉淀入库失败：{ex.Message}";
        }
        finally
        {
            message.IsIngesting = false;
        }
    }

    /// <summary>插入场景化快捷指令模板。</summary>
    [RelayCommand]
    private void InsertPromptTemplate(string? templateType)
    {
        var prefix = templateType switch
        {
            "summary" => "请将上述内容萃取提炼为核心结论与清晰的 Action Items 待办清单：\n",
            "table" => "请以结构化 Markdown 表格形式，全方位对比各方案的优缺点、适用场景与成本效益：\n",
            "polish" => "请将以下草稿按严谨专业的企业公文与技术汇报规范进行润色重构：\n",
            "pitfall" => "请对以下方案进行专家级把关评审，列出潜在风险点、性能隐患与避坑防范建议：\n",
            _ => string.Empty
        };

        if (string.IsNullOrEmpty(InputText))
        {
            InputText = prefix;
        }
        else
        {
            InputText = prefix + InputText;
        }
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

    /// <summary>发送核心：添加用户消息（可选）+ 流式请求 + 终帧回写。Send 与 Regenerate 共用。</summary>
    private async Task SendCoreAsync(string query, bool addUserMessage)
    {
        // 添加用户消息
        if (addUserMessage)
        {
            Messages.Add(new ChatMessage { Role = "user", Content = query });
        }

        // 添加流式占位（先空内容，逐 token 追加）
        var assistantMsg = new ChatMessage { Role = "assistant", Content = "", IsLoading = true, IsWaitingForFirstToken = true };
        Messages.Add(assistantMsg);
        
        // 发送开始前，清空输入框
        InputText = string.Empty;

        IsBusy = true;
        ShowStopChanged();
        StatusMessage = "对话中…";
        var sw = System.Diagnostics.Stopwatch.StartNew();

        _cts = new CancellationTokenSource();
        // 流式排查统计：token 帧数 + 首 token 延迟（TTFT）
        var tokenCount = 0;
        long firstTokenMs = -1;
        try
        {
            var selected = SelectedCollections;
            DebugLog.Info(
                $"发送消息: query='{(query.Length > 100 ? query[..100] + "…" : query)}' " +
                $"collections=[{string.Join(",", selected)}] chatId='{_chatId ?? "-"}' model='{(SelectedModel == DefaultModelLabel ? "-" : SelectedModel)}' persona='{SelectedPersona?.Id ?? "-"}' msgCount={Messages.Count}",
                "Chat");
            ChatStreamResult? final = null;
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
                    Model = SelectedModel == DefaultModelLabel ? null : SelectedModel,
                    Persona = SelectedPersona?.Id,
                },
                onToken: token =>
                {
                    if (tokenCount == 0)
                    {
                        firstTokenMs = sw.ElapsedMilliseconds;
                    }
                    tokenCount++;
                    if (assistantMsg.IsWaitingForFirstToken)
                    {
                        assistantMsg.IsWaitingForFirstToken = false;
                        assistantMsg.Content = token;
                    }
                    else
                    {
                        assistantMsg.Content += token;
                    }
                },
                onDone: result =>
                {
                    final = result;
                    // 终帧后强制重新解析 Markdown,确保最终渲染完整(不受流式节流影响)
                    assistantMsg.ForceRefreshRender();
                },
                ct: _cts.Token);

            sw.Stop();
            DebugLog.Info(
                $"流式统计: tokens={tokenCount} 首token={(firstTokenMs >= 0 ? $"{firstTokenMs}ms" : "未收到")} " +
                $"收到done帧={(final is not null ? "是" : "否")} 总耗时{sw.ElapsedMilliseconds}ms",
                "Chat");

            // 终帧：回写多轮 chat_id + 来源 + 元数据（流式多轮不中断的关键）
            if (final is not null)
            {
                var isNewChat = _chatId is null && !string.IsNullOrEmpty(final.ChatId);
                _chatId = final.ChatId ?? _chatId;
                assistantMsg.Sources = final.Sources;
                assistantMsg.Model = final.Model;
                assistantMsg.Provider = final.Provider;
                assistantMsg.ElapsedMs = final.ElapsedMs;

                StatusMessage = $"模型: {final.Model} ({final.Provider}) · 引用 {final.TotalChunks} 块 · 耗时 {final.ElapsedMs}ms";
                DebugLog.Info($"对话完成(流式): elapsed={final.ElapsedMs}ms model={final.Model} chunks={final.TotalChunks} sources={final.Sources.Count} chatId='{final.ChatId}'", "Chat");

                // 新会话首条回答完成 → 刷新会话列表（标题/条数已生成），选中当前会话
                if (isNewChat)
                {
                    _ = LoadSessionsAsync(selectChatId: _chatId);
                }
            }
            else
            {
                DebugLog.Warn("未收到 done 终帧：多轮 chat_id 未更新、来源/模型元数据缺失（后端可能异常中断流）", "Chat");
                StatusMessage = $"对话完成 · 耗时 {sw.ElapsedMilliseconds}ms";
            }

            assistantMsg.IsLoading = false;
            if (string.IsNullOrEmpty(assistantMsg.Content))
            {
                assistantMsg.Content = "（无内容返回）";
            }
            UpdateMessageFlags();
        }
        catch (OperationCanceledException)
        {
            sw.Stop();
            StatusMessage = "已停止生成";
            DebugLog.Info("对话已停止", "Chat");
            if (string.IsNullOrEmpty(assistantMsg.Content))
            {
                assistantMsg.Content = "（已停止生成）";
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
            assistantMsg.Content = hint is null
                ? $"❌ API 错误：{ex.Message}"
                : $"❌ {hint}";
            // 发送失败时，将原文回填到输入框，避免草稿丢失
            InputText = query;
        }
        catch (BackendConnectionException ex)
        {
            sw.Stop();
            StatusMessage = "无法连接到后端";
            DebugLog.Error($"对话连接失败: {ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.IsWaitingForFirstToken = false;
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
            assistantMsg.Content = $"❌ 错误：{ex.Message}";
            InputText = query;
        }
        finally
        {
            _cts?.Dispose();
            _cts = null;
            IsBusy = false;
            ShowStopChanged();
            OnPropertyChanged(nameof(HasMessages));
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

    /// <summary>撤回用户消息并回填到输入框（可传入特定消息，默认撤回最后一条用户消息）。</summary>
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
        // 移除该用户消息及其后的所有消息（如对应的 AI 回答）
        while (Messages.Count > userIdx)
        {
            Messages.RemoveAt(Messages.Count - 1);
        }

        InputText = content;
        StatusMessage = "已撤回消息并回填至输入框";
        DebugLog.Info($"已撤回用户消息: '{content}'", "Chat");
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
        }
    }

    /// <summary>拉取历史会话列表（后端不可达时静默）。selectChatId 非空时选中该会话。</summary>
    private async Task LoadSessionsAsync(string? selectChatId = null)
    {
        try
        {
            var list = await _apiService.ListChatsAsync(limit: 50);
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
            DebugLog.Info($"会话列表加载完成: {Sessions.Count} 个", "Chat");
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
                Messages.Add(new ChatMessage
                {
                    Role = m.Role,
                    Content = m.Content,
                    Sources = m.Sources,
                });
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

    private SourceRef? _selectedSource;
    private bool _isSourceDrawerOpen;

    /// <summary>当前选中的引用来源（供右侧协同抽屉预览）。</summary>
    public SourceRef? SelectedSource
    {
        get => _selectedSource;
        set
        {
            if (SetProperty(ref _selectedSource, value))
            {
                OnPropertyChanged(nameof(HasSelectedSource));
                OnPropertyChanged(nameof(SelectedSourceTitle));
                OnPropertyChanged(nameof(SelectedSourceSnippet));
            }
        }
    }

    public bool HasSelectedSource => SelectedSource != null;
    public string SelectedSourceTitle => SelectedSource?.DisplayTitle ?? "(未命名来源)";
    public string SelectedSourceSnippet => !string.IsNullOrWhiteSpace(SelectedSource?.Snippet)
        ? SelectedSource.Snippet
        : "（该切片暂无全文预览或来自早期版本会话，可通过下方动作查看原文）";

    /// <summary>协同来源预览抽屉是否展开。</summary>
    public bool IsSourceDrawerOpen
    {
        get => _isSourceDrawerOpen;
        set => SetProperty(ref _isSourceDrawerOpen, value);
    }

    /// <summary>点击引用来源：在右侧协同抽屉就地展开原著切片与元数据，不离开对话主界面。</summary>
    [RelayCommand]
    private void OpenSource(SourceRef? src)
    {
        if (src is null)
        {
            return;
        }
        SelectedSource = src;
        IsSourceDrawerOpen = true;
        StatusMessage = $"查看切片出处：{src.DisplayTitle} (相似度: {src.Score:F2})";
        DebugLog.Info($"展开引用来源抽屉: index={src.Index} source={src.Source} page={src.Page}", "Chat");
    }

    /// <summary>关闭协同来源抽屉。</summary>
    [RelayCommand]
    private void CloseSourceDrawer()
    {
        IsSourceDrawerOpen = false;
    }

    /// <summary>复制当前抽屉中切片正文到剪贴板。</summary>
    [RelayCommand]
    private void CopySourceSnippet()
    {
        if (!string.IsNullOrWhiteSpace(SelectedSource?.Snippet))
        {
            try
            {
                Clipboard.SetText(SelectedSource.Snippet);
                _notifications?.Success("已复制切片原文到剪贴板");
                StatusMessage = "已复制切片原文到剪贴板";
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"复制到剪贴板失败: {ex.Message}", "Chat");
            }
        }
    }

    /// <summary>用户主动选择：在知识搜索页全文检索该文档（深钻次级动作）。</summary>
    [RelayCommand]
    private void SearchSourceInSearchPage()
    {
        if (SelectedSource is not null)
        {
            SourceSearchRequested?.Invoke(SelectedSource);
        }
    }

    /// <summary>对话页拉取模型列表（用后端运行时配置：设置页已保存的 provider/key/地址）。</summary>
    [RelayCommand]
    private async Task RefreshModelsAsync()
    {
        try
        {
            var result = await _apiService.LlmModelsAsync(new LlmModelsRequest { Timeout = 10 });
            if (!result.Ok)
            {
                StatusMessage = $"❌ 获取模型列表失败: {result.Error ?? "未知错误"}";
                DebugLog.Warn($"对话页获取模型列表失败: {result.Error}", "Chat");
                return;
            }

            var current = SelectedModel;
            AvailableModels.Clear();
            AvailableModels.Add(DefaultModelLabel);
            foreach (var m in result.Models)
            {
                if (!string.IsNullOrWhiteSpace(m))
                {
                    AvailableModels.Add(m);
                }
            }
            // 保留用户此前选择（已不在列表中则回到默认）
            SelectedModel = AvailableModels.Contains(current) ? current : DefaultModelLabel;
            StatusMessage = $"✅ 获取到 {result.Models.Count} 个模型（{result.Provider}）";
            DebugLog.Info($"对话页模型列表: provider={result.Provider} count={result.Models.Count}", "Chat");
        }
        catch (Exception ex)
        {
            StatusMessage = $"❌ 获取模型列表失败: {ex.Message}";
            DebugLog.Warn($"对话页获取模型列表异常: {ex.Message}", "Chat");
        }
    }

    /// <summary>种子模型：从后端配置取当前 llm_model 加入候选（未拉列表前至少能看到配置值）。</summary>
    private async Task SeedModelFromConfigAsync()
    {
        try
        {
            var cfg = await _apiService.GetConfigAsync();
            var model = cfg?.LlmModel;
            if (!string.IsNullOrWhiteSpace(model) && !AvailableModels.Contains(model))
            {
                AvailableModels.Add(model);
            }
        }
        catch (Exception ex)
        {
            DebugLog.Debug($"读取后端配置种子模型失败（忽略）: {ex.Message}", "Chat");
        }
    }

    /// <summary>测试用：等待会话列表加载（构造时 fire-and-forget 不可 await）。</summary>
    internal Task SessionsLoadedForTestAsync() => LoadSessionsAsync();

    /// <summary>测试用：等待选中会话的消息加载完成。</summary>
    internal Task SessionLoadedForTestAsync()
        => SelectedSession is null ? Task.CompletedTask : LoadSessionMessagesAsync(SelectedSession);
}