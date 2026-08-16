using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

/// <summary>单条对话消息（用户或助手）。</summary>
public sealed record ChatMessage
{
    /// <summary>角色：user / assistant / system。</summary>
    public required string Role { get; init; }

    /// <summary>消息内容。</summary>
    public required string Content { get; init; }

    /// <summary>引用来源（仅 assistant 有）。</summary>
    public IReadOnlyList<SourceRef>? Sources { get; init; }

    /// <summary>模型名（仅 assistant 有）。</summary>
    public string? Model { get; init; }

    /// <summary>提供商标识（仅 assistant 有）。</summary>
    public string? Provider { get; init; }

    /// <summary>耗时 ms（仅 assistant 有）。</summary>
    public int? ElapsedMs { get; init; }

    /// <summary>是否正在加载（骨架屏用）。</summary>
    public bool IsLoading { get; init; }

    /// <summary>是否有引用来源（控制来源列表可见性）。</summary>
    public bool HasSources => Sources is { Count: > 0 };

    /// <summary>是否有模型信息（仅在 assistant 回答中显示）。</summary>
    public bool HasModel => Model is not null;

    /// <summary>是否来自用户（UI 分左右用）。</summary>
    public bool IsUser => Role == "user";
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

public partial class ChatViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;

    private string _inputText = string.Empty;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private string? _chatId;
    private int _topK = 5;

    public ChatViewModel(IDoc2kbApiService apiService)
    {
        _apiService = apiService;
        Title = "对话";

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

        Messages.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(ShowEmptyGuide));
            OnPropertyChanged(nameof(EmptyGuideText));
        };

        // 构造时拉取已有知识库集合
        _ = LoadCollectionsAsync();
    }

    /// <summary>可勾选的知识库集合列表（复选框）。</summary>
    public ObservableCollection<CollectionItem> Collections { get; }

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
                SendCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>引用 chunk 数。</summary>
    public int TopK
    {
        get => _topK;
        set => SetProperty(ref _topK, value);
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

            Collections.Clear();
            var list = names.ToList();
            list.Sort(StringComparer.OrdinalIgnoreCase);
            foreach (var name in list)
            {
                // 默认勾选 default 集合
                var isSelected = string.Equals(name, "default", StringComparison.OrdinalIgnoreCase);
                Collections.Add(new CollectionItem { Name = name, IsSelected = isSelected });
            }

            OnPropertyChanged(nameof(HasSelectedCollection));
            SendCommand.NotifyCanExecuteChanged();
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
        + "💡 需要先配置 LLM：\n"
        + "  设置 DOC2MIND_LLM_PROVIDER=openai\n"
        + "  或运行 doc2mind config 持久化配置";

    private bool CanSend => !IsBusy && HasInput;

    /// <summary>发送消息。</summary>
    [RelayCommand(CanExecute = nameof(CanSend))]
    private async Task SendAsync()
    {
        var query = InputText.Trim();
        if (string.IsNullOrWhiteSpace(query))
            return;

        // 添加用户消息
        Messages.Add(new ChatMessage { Role = "user", Content = query });
        InputText = string.Empty;

        // 添加加载占位
        var loadingMsg = new ChatMessage { Role = "assistant", Content = "思考中…", IsLoading = true };
        Messages.Add(loadingMsg);

        IsBusy = true;
        StatusMessage = "对话中…";
        var sw = System.Diagnostics.Stopwatch.StartNew();

        try
        {
            var selected = SelectedCollections;
            var resp = await _apiService.ChatAsync(
                new ChatRequest
                {
                    Query = query,
                    Collections = selected.Count > 0 ? selected : null,
                    TopK = TopK,
                    ChatId = _chatId,
                });

            sw.Stop();

            // 更新 chat_id（首轮后锁定）
            _chatId = resp.ChatId ?? _chatId;

            // 替换加载占位为真实回答
            var index = Messages.IndexOf(loadingMsg);
            if (index >= 0)
            {
                Messages[index] = new ChatMessage
                {
                    Role = "assistant",
                    Content = resp.Answer,
                    Sources = resp.Sources,
                    Model = resp.Model,
                    Provider = resp.Provider,
                    ElapsedMs = resp.ElapsedMs,
                };
            }

            StatusMessage = $"模型: {resp.Model} ({resp.Provider}) · 引用 {resp.TotalChunks} 块 · 耗时 {resp.ElapsedMs}ms";
            DebugLog.Info($"对话完成: elapsed={resp.ElapsedMs}ms model={resp.Model} chunks={resp.TotalChunks}", "Chat");
        }
        catch (ApiException ex)
        {
            sw.Stop();
            StatusMessage = $"API 错误：{ex.Message}";
            DebugLog.Error($"对话 API 错误: code={ex.Code} message={ex.Message}", "Chat", ex);
            // 替换加载占位为错误消息
            var index = Messages.IndexOf(loadingMsg);
            if (index >= 0)
            {
                Messages[index] = new ChatMessage
                {
                    Role = "assistant",
                    Content = $"❌ API 错误：{ex.Message}",
                };
            }
        }
        catch (BackendConnectionException ex)
        {
            sw.Stop();
            StatusMessage = $"后端不可达：{ex.Message}";
            DebugLog.Error($"对话后端不可达: {ex.Message}", "Chat", ex);
            var index = Messages.IndexOf(loadingMsg);
            if (index >= 0)
            {
                Messages[index] = new ChatMessage
                {
                    Role = "assistant",
                    Content = $"❌ 后端不可达：{ex.Message}",
                };
            }
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"对话未知异常", "Chat", ex);
            var index = Messages.IndexOf(loadingMsg);
            if (index >= 0)
            {
                Messages[index] = new ChatMessage
                {
                    Role = "assistant",
                    Content = $"❌ 错误：{ex.Message}",
                };
            }
        }
        finally
        {
            IsBusy = false;
            OnPropertyChanged(nameof(HasMessages));
        }
    }

    /// <summary>清空对话。</summary>
    [RelayCommand]
    private void Clear()
    {
        Messages.Clear();
        _chatId = null;
        StatusMessage = "就绪";
        OnPropertyChanged(nameof(HasMessages));
    }
}