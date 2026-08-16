using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

/// <summary>单条对话消息（用户或助手）。可变 class 以支持流式增量追加 token。</summary>
public sealed class ChatMessage : System.ComponentModel.INotifyPropertyChanged
{
    private string _role = string.Empty;
    private string _content = string.Empty;
    private IReadOnlyList<SourceRef>? _sources;
    private string? _model;
    private string? _provider;
    private int? _elapsedMs;
    private bool _isLoading;

    /// <summary>角色：user / assistant / system。</summary>
    public string Role
    {
        get => _role;
        set => SetField(ref _role, value);
    }

    /// <summary>消息内容。</summary>
    public string Content
    {
        get => _content;
        set => SetField(ref _content, value);
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

    /// <summary>是否正在加载（骨架屏用）。</summary>
    public bool IsLoading
    {
        get => _isLoading;
        set => SetField(ref _isLoading, value);
    }

    /// <summary>是否有引用来源（控制来源列表可见性）。</summary>
    public bool HasSources => Sources is { Count: > 0 };

    /// <summary>是否有模型信息（仅在 assistant 回答中显示）。</summary>
    public bool HasModel => Model is not null;

    /// <summary>是否来自用户（UI 分左右用）。</summary>
    public bool IsUser => Role == "user";

    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;

    private void SetField<T>(ref T field, T value, [System.Runtime.CompilerServices.CallerMemberName] string? propertyName = null)
    {
        if (!System.Collections.Generic.EqualityComparer<T>.Default.Equals(field, value))
        {
            field = value;
            PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(propertyName));
        }
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
                OnPropertyChanged(nameof(ShowStop));
                SendCommand.NotifyCanExecuteChanged();
                StopCommand.NotifyCanExecuteChanged();
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

        // 添加用户消息
        Messages.Add(new ChatMessage { Role = "user", Content = query });
        InputText = string.Empty;

        // 添加流式占位（先空内容，逐 token 追加）
        var assistantMsg = new ChatMessage { Role = "assistant", Content = "", IsLoading = true };
        Messages.Add(assistantMsg);

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
                $"collections=[{string.Join(",", selected)}] topK={TopK} chatId='{_chatId ?? "-"}' msgCount={Messages.Count}",
                "Chat");
            ChatStreamResult? final = null;
            await _apiService.ChatStreamAsync(
                new ChatRequest
                {
                    Query = query,
                    Collections = selected.Count > 0 ? selected : null,
                    TopK = TopK,
                    ChatId = _chatId,
                },
                onToken: token =>
                {
                    if (tokenCount == 0)
                    {
                        firstTokenMs = sw.ElapsedMilliseconds;
                    }
                    tokenCount++;
                    // 首 token 到达即结束加载态，开始增量显示
                    if (assistantMsg.IsLoading)
                    {
                        assistantMsg.IsLoading = false;
                        assistantMsg.Content = token;
                    }
                    else
                    {
                        assistantMsg.Content += token;
                    }
                },
                onDone: result => { final = result; },
                ct: _cts.Token);

            sw.Stop();
            DebugLog.Info(
                $"流式统计: tokens={tokenCount} 首token={(firstTokenMs >= 0 ? $"{firstTokenMs}ms" : "未收到")} " +
                $"收到done帧={(final is not null ? "是" : "否")} 总耗时{sw.ElapsedMilliseconds}ms",
                "Chat");

            // 终帧：回写多轮 chat_id + 来源 + 元数据（流式多轮不中断的关键）
            if (final is not null)
            {
                _chatId = final.ChatId ?? _chatId;
                assistantMsg.Sources = final.Sources;
                assistantMsg.Model = final.Model;
                assistantMsg.Provider = final.Provider;
                assistantMsg.ElapsedMs = final.ElapsedMs;

                StatusMessage = $"模型: {final.Model} ({final.Provider}) · 引用 {final.TotalChunks} 块 · 耗时 {final.ElapsedMs}ms";
                DebugLog.Info($"对话完成(流式): elapsed={final.ElapsedMs}ms model={final.Model} chunks={final.TotalChunks} sources={final.Sources.Count} chatId='{final.ChatId}'", "Chat");
            }
            else
            {
                DebugLog.Warn("未收到 done 终帧：多轮 chat_id 未更新、来源/模型元数据缺失（后端可能异常中断流）", "Chat");
                StatusMessage = $"对话完成 · 耗时 {sw.ElapsedMilliseconds}ms";
            }

            if (string.IsNullOrEmpty(assistantMsg.Content))
            {
                assistantMsg.Content = "（无内容返回）";
            }
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
            StatusMessage = $"API 错误：{ex.Message}";
            DebugLog.Error($"对话 API 错误: code={ex.Code} message={ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.Content = $"❌ API 错误：{ex.Message}";
        }
        catch (BackendConnectionException ex)
        {
            sw.Stop();
            StatusMessage = $"后端不可达：{ex.Message}";
            DebugLog.Error($"对话后端不可达: {ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.Content = $"❌ 后端不可达：{ex.Message}";
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"对话未知异常: {ex.GetType().Name}: {ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.Content = $"❌ 错误：{ex.Message}";
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

    /// <summary>清空对话。</summary>
    [RelayCommand]
    private void Clear()
    {
        DebugLog.Info($"清空对话: messages={Messages.Count} chatId='{_chatId ?? "-"}'", "Chat");
        // 进行中则先停止
        try { _cts?.Cancel(); } catch { /* ignore */ }
        Messages.Clear();
        _chatId = null;
        StatusMessage = "就绪";
        OnPropertyChanged(nameof(HasMessages));
    }
}