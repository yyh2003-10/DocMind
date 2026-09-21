using System.Collections.ObjectModel;
using System.Text;
using System.Windows;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class SearchViewModel : ViewModelBase
{

    /// <summary>后端不可达时通知 Main 刷新全局离线横幅（FC-03/08）。</summary>
    public event Action? BackendUnreachable;

    private readonly IDoc2kbApiService _apiService;
    private readonly AppSettings? _appSettings;

    /// <summary>用户点击「在文档库中查看」事件（参数为文档来源路径/名称）。</summary>
    public event Action<string>? OpenDocumentRequested;

    /// <summary>用户点击「基于此分块提问」事件（参数为提问引导内容）。</summary>
    public event Action<string>? AskInChatRequested;

    private string _query = string.Empty;
    private string? _collection;
    private int _topK = 10;
    private double? _minScore;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private SearchResponse? _lastResponse;
    private SearchHit? _selectedHit;
    private bool _showHistory;

    public const string AllCollectionsLabel = "(全部集合)";
    private const int MaxSearchHistory = 20;

    public SearchViewModel(IDoc2kbApiService apiService, AppSettings? appSettings = null)
    {
        _apiService = apiService;
        _appSettings = appSettings;
        Title = "搜索";
        Hits = new ObservableCollection<SearchHit>();
        AvailableCollections = new ObservableCollection<string> { AllCollectionsLabel, "default" };
        SearchHistory = new ObservableCollection<string>(_appSettings?.SearchHistory ?? new List<string>());

        // 结果列表变化 → 刷新空态引导与结果状态可见性
        Hits.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(ShowEmptyGuide));
            OnPropertyChanged(nameof(EmptyGuideText));
            OnPropertyChanged(nameof(HasHits));
        };

        _ = LoadCollectionsAsync();
    }

    /// <summary>可选的集合列表（含全部集合选项及后端已存在集合）。</summary>
    public ObservableCollection<string> AvailableCollections { get; }

    /// <summary>异步从后端拉取现有集合列表。</summary>
    public async Task LoadCollectionsAsync()
    {
        try
        {
            var stats = await _apiService.GetStatsAsync();
            if (stats?.Collections != null)
            {
                var current = Collection;
                AvailableCollections.Clear();
                AvailableCollections.Add(AllCollectionsLabel);
                foreach (var col in stats.Collections.Keys.OrderBy(k => k))
                {
                    AvailableCollections.Add(col);
                }

                if (!string.IsNullOrWhiteSpace(current) && AvailableCollections.Contains(current))
                {
                    Collection = current;
                }
                else
                {
                    Collection = AllCollectionsLabel;
                }
            }
        }
        catch
        {
            // 离线或初次加载失败时静默使用默认项
        }
    }

    /// <summary>搜索词。</summary>
    public string Query
    {
        get => _query;
        set
        {
            if (SetProperty(ref _query, value))
            {
                OnPropertyChanged(nameof(HasQuery));
                OnPropertyChanged(nameof(ShowEmptyGuide));
                OnPropertyChanged(nameof(EmptyGuideText));
            }
        }
    }

    /// <summary>是否有搜索词（用于 UI 显示清除按钮等）。</summary>
    public bool HasQuery => !string.IsNullOrWhiteSpace(Query);

    /// <summary>是否有返回命中结果。</summary>
    public bool HasHits => Hits.Count > 0;

    // ===== 搜索历史 =====

    /// <summary>历史搜索词列表（最新在前，上限 20 条）。</summary>
    public ObservableCollection<string> SearchHistory { get; }

    /// <summary>是否有搜索历史。</summary>
    public bool HasSearchHistory => SearchHistory.Count > 0;

    /// <summary>是否显示搜索历史下拉面板。</summary>
    public bool ShowHistory
    {
        get => _showHistory;
        set => SetProperty(ref _showHistory, value);
    }

    /// <summary>把搜索词加入历史（去重 + 最新在前 + 上限 20）。</summary>
    private void AddToHistory(string query)
    {
        var trimmed = query.Trim();
        if (string.IsNullOrWhiteSpace(trimmed)) return;

        // 去重（不区分大小写）
        var existing = SearchHistory.FirstOrDefault(h =>
            string.Equals(h, trimmed, StringComparison.OrdinalIgnoreCase));
        if (existing != null)
            SearchHistory.Remove(existing);

        SearchHistory.Insert(0, trimmed);

        // 超限移除末尾
        while (SearchHistory.Count > MaxSearchHistory)
            SearchHistory.RemoveAt(SearchHistory.Count - 1);

        OnPropertyChanged(nameof(HasSearchHistory));
        PersistHistory();
    }

    /// <summary>删除单条历史记录。</summary>
    [RelayCommand]
    private void RemoveHistoryItem(string? item)
    {
        if (string.IsNullOrWhiteSpace(item)) return;
        SearchHistory.Remove(item);
        OnPropertyChanged(nameof(HasSearchHistory));
        PersistHistory();
    }

    /// <summary>清空全部搜索历史。</summary>
    [RelayCommand]
    private void ClearHistory()
    {
        SearchHistory.Clear();
        OnPropertyChanged(nameof(HasSearchHistory));
        PersistHistory();
    }

    /// <summary>选中历史记录项 → 填入搜索框并执行搜索。</summary>
    [RelayCommand]
    private void SelectHistoryItem(string? item)
    {
        if (string.IsNullOrWhiteSpace(item)) return;
        Query = item;
        ShowHistory = false;
        _ = SearchAsync();
    }

    /// <summary>切换历史面板显示。</summary>
    [RelayCommand]
    private void ToggleHistory() => ShowHistory = !ShowHistory;

    private void PersistHistory()
    {
        if (_appSettings == null) return;
        _appSettings.SearchHistory = SearchHistory.ToList();
        try { _appSettings.Save(); } catch { /* 落盘失败不阻断搜索 */ }
    }

    // ===== 高亮辅助 =====

    /// <summary>把文本中的搜索关键词用 ⌜⌟ 标记包裹（纯文本高亮标记，供 UI 层解析渲染）。</summary>
    public static string HighlightTerms(string text, string? query)
    {
        if (string.IsNullOrWhiteSpace(text) || string.IsNullOrWhiteSpace(query))
            return text;

        var terms = query.Split(new[] { ' ', '\t', '，', ',' }, StringSplitOptions.RemoveEmptyEntries);
        if (terms.Length == 0) return text;

        var result = text;
        foreach (var term in terms.Where(t => t.Length >= 2))
        {
            // 大小写不敏感替换，用标记包裹
            var idx = 0;
            var sb = new System.Text.StringBuilder();
            while (idx < result.Length)
            {
                var found = result.IndexOf(term, idx, StringComparison.OrdinalIgnoreCase);
                if (found < 0)
                {
                    sb.Append(result.AsSpan(idx));
                    break;
                }
                sb.Append(result.AsSpan(idx, found - idx));
                sb.Append('\u231c'); // ⌜
                sb.Append(result.AsSpan(found, term.Length));
                sb.Append('\u231d'); // ⌝
                idx = found + term.Length;
            }
            result = sb.ToString();
        }
        return result;
    }

    /// <summary>结果区空态是否可见（非忙碌且无结果）。</summary>
    public bool ShowEmptyGuide => !IsBusy && Hits.Count == 0;

    /// <summary>FC-07：最近一次空结果时后端给出的差异化提示（库为空/集合空/无命中）。</summary>
    private string? _lastEmptyHint;

    /// <summary>FC-07：是否属于「知识库/集合为空」类空态（应引导去导入，而不是换词）。</summary>
    private bool _isLibraryEmpty;

    /// <summary>空态时是否显示「去导入」动作（FC-07）。</summary>
    public bool ShowGoImportAction => ShowEmptyGuide && _isLibraryEmpty;

    /// <summary>请求跳转到导入页（MainViewModel 订阅）。</summary>
    public event Action? GoToImportRequested;

    /// <summary>导航到导入页。</summary>
    [RelayCommand]
    private void GoToImport() => GoToImportRequested?.Invoke();

    /// <summary>空态引导文案：区分"还没搜过 / 知识库为空 / 有文档无命中"（FC-07）。</summary>
    public string EmptyGuideText
    {
        get
        {
            if (!HasQuery)
            {
                return "输入问题或关键词开始搜索。\nDocMind 将基于向量语义与关键词进行混合检索。\n还没导入文档？先到【导入】页添加文件。";
            }
            if (_isLibraryEmpty)
            {
                var head = string.IsNullOrWhiteSpace(_lastEmptyHint)
                    ? "知识库为空：请先在【导入】页添加文档"
                    : _lastEmptyHint;
                return head + "\n\n下一步：点击「去导入」添加文件，完成后再回来搜索。";
            }
            if (!string.IsNullOrWhiteSpace(_lastEmptyHint))
            {
                return _lastEmptyHint + "\n\n建议：更换关键词，或调低「最低相似度」后重试。";
            }
            return "没有匹配的结果。\n建议：尝试更换关键词，或调低「最低相似度」；\n也可以到【导入】页确认文档已加入知识库。";
        }
    }

    /// <summary>集合名（可选，AllCollectionsLabel 或空表示全部）。</summary>
    public string? Collection
    {
        get => _collection;
        set => SetProperty(ref _collection, value);
    }

    /// <summary>Top-K 结果数。</summary>
    public int TopK
    {
        get => _topK;
        set => SetProperty(ref _topK, value);
    }

    /// <summary>最低相似度阈值（可选，null = 不过滤）。</summary>
    public double? MinScore
    {
        get => _minScore;
        set => SetProperty(ref _minScore, value);
    }

    /// <summary>是否正在请求中。</summary>
    public bool IsBusy
    {
        get => _isBusy;
        set
        {
            if (SetProperty(ref _isBusy, value))
            {
                SearchCommand.NotifyCanExecuteChanged();
                OnPropertyChanged(nameof(ShowEmptyGuide));
            }
        }
    }

    /// <summary>底部状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>上次响应（用于显示 elapsed / total）。</summary>
    public SearchResponse? LastResponse
    {
        get => _lastResponse;
        set
        {
            if (SetProperty(ref _lastResponse, value))
                OnPropertyChanged(nameof(HasResults));
        }
    }

    /// <summary>是否有搜索结果（用于统计栏可见性）。</summary>
    public bool HasResults => LastResponse != null;

    /// <summary>当前选中 hit，详情区显示。</summary>
    public SearchHit? SelectedHit
    {
        get => _selectedHit;
        set
        {
            if (SetProperty(ref _selectedHit, value))
            {
                OnPropertyChanged(nameof(HasSelectedHit));
                OnPropertyChanged(nameof(HasNoSelectedHit));
                OpenInDocumentsCommand.NotifyCanExecuteChanged();
                AskInChatCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>是否有选中的搜索结果（用于详情区可见性）。</summary>
    public bool HasSelectedHit => SelectedHit != null;

    /// <summary>未选中结果时显示详情区空态提示。</summary>
    public bool HasNoSelectedHit => SelectedHit is null;

    /// <summary>搜索结果列表。</summary>
    public ObservableCollection<SearchHit> Hits { get; }

    private bool CanSearch => !IsBusy && !string.IsNullOrWhiteSpace(Query);

    /// <summary>执行搜索。</summary>
    [RelayCommand(CanExecute = nameof(CanSearch))]
    private async Task SearchAsync()
    {
        if (!CanSearch)
        {
            return;
        }

        IsBusy = true;
        StatusMessage = "搜索中…";
        Hits.Clear();
        SelectedHit = null;

        var targetCollection = (string.IsNullOrWhiteSpace(Collection) || Collection == AllCollectionsLabel)
            ? null
            : Collection.Trim();

        DebugLog.Info($"开始搜索: Query='{Query.Trim()}' Collection='{(targetCollection ?? "(全部)")}' TopK={TopK}", "Search");
        var sw = System.Diagnostics.Stopwatch.StartNew();

        try
        {
            // 使用 ToolCallResilience：搜索空结果时自动放宽阈值、扩大范围重试
            var query = Query.Trim();
            var resp = await ToolCallResilience.SearchWithHealingAsync<SearchResponse>(
                searchFn: (topK, minScore) => _apiService.SearchAsync(
                    new SearchRequest
                    {
                        Query = query,
                        Collection = targetCollection,
                        TopK = topK,
                        MinScore = minScore,
                    }),
                query: query,
                topK: TopK,
                minScore: MinScore,
                maxRetries: 2);

            sw.Stop();
            LastResponse = resp;
            foreach (var hit in resp.Hits)
            {
                Hits.Add(hit);
            }

            if (Hits.Count > 0)
            {
                SelectedHit = Hits[0];
                _lastEmptyHint = null;
                _isLibraryEmpty = false;
            }
            else
            {
                // FC-07：消费后端差异化空态 message
                _lastEmptyHint = string.IsNullOrWhiteSpace(resp.Message) ? null : resp.Message.Trim();
                _isLibraryEmpty = _lastEmptyHint is not null
                    && (_lastEmptyHint.Contains("知识库为空", StringComparison.Ordinal)
                        || _lastEmptyHint.Contains("没有任何文档", StringComparison.Ordinal)
                        || (_lastEmptyHint.Contains("集合", StringComparison.Ordinal)
                            && _lastEmptyHint.Contains("文档", StringComparison.Ordinal)));
            }

            // 搜索成功后记录历史
            AddToHistory(Query.Trim());

            StatusMessage = !string.IsNullOrWhiteSpace(resp.Message)
                ? resp.Message + (resp.Degraded ? "（嵌入不可用，仅关键词检索）" : "")
                : resp.Total > 0
                    ? $"返回 {resp.Hits.Count}/{resp.Total} 条 · 耗时 {resp.ElapsedMs:F0}ms" + (resp.Degraded ? "（嵌入不可用，仅关键词检索）" : "")
                    : "无匹配结果：请尝试更换关键词或调低最低相似度阈值";

            OnPropertyChanged(nameof(EmptyGuideText));
            OnPropertyChanged(nameof(ShowEmptyGuide));
            OnPropertyChanged(nameof(ShowGoImportAction));

            DebugLog.Info($"搜索完成: hits={resp.Hits.Count} total={resp.Total} elapsed={resp.ElapsedMs:F0}ms 本地耗时{sw.ElapsedMilliseconds}ms emptyHint='{_lastEmptyHint}'", "Search");
        }
        catch (ApiException ex)
        {
            sw.Stop();
            StatusMessage = $"API 错误：{ex.Message}";
            DebugLog.Error($"搜索 API 错误: code={ex.Code} message={ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Search", ex);
        }
        catch (BackendConnectionException ex)
        {
            BackendUnreachable?.Invoke();
            sw.Stop();
            StatusMessage = $"后端不可达：{ex.Message}";
            DebugLog.Error($"搜索后端不可达: {ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Search", ex);
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"搜索未知异常 耗时{sw.ElapsedMilliseconds}ms", "Search", ex);
        }
        finally
        {
            IsBusy = false;
            DebugLog.Info($"搜索流程结束，总耗时{sw.ElapsedMilliseconds}ms", "Search");
        }
    }

    /// <summary>供其他页面调用：设置搜索词并立即执行（如文档详情分块定位）。</summary>
    public void SearchWithQuery(string query)
    {
        if (string.IsNullOrWhiteSpace(query))
        {
            return;
        }

        Query = query;
        DebugLog.Info($"跨页发起搜索: Query='{query.Trim()}'", "Search");
        _ = SearchAsync();
    }

    /// <summary>清空搜索词与结果。</summary>
    [RelayCommand]
    private void Clear()
    {
        Query = string.Empty;
        Hits.Clear();
        SelectedHit = null;
        LastResponse = null;
        _lastEmptyHint = null;
        _isLibraryEmpty = false;
        StatusMessage = "就绪";
        OnPropertyChanged(nameof(EmptyGuideText));
        OnPropertyChanged(nameof(ShowEmptyGuide));
        OnPropertyChanged(nameof(ShowGoImportAction));
    }

    /// <summary>跳转至文档库查看该文档。</summary>
    [RelayCommand(CanExecute = nameof(HasSelectedHit))]
    private void OpenInDocuments()
    {
        if (SelectedHit != null && !string.IsNullOrWhiteSpace(SelectedHit.Source))
        {
            OpenDocumentRequested?.Invoke(SelectedHit.Source);
        }
    }

    /// <summary>基于当前分块内容跳转到对话页发起提问。</summary>
    [RelayCommand(CanExecute = nameof(HasSelectedHit))]
    private void AskInChat()
    {
        if (SelectedHit != null && !string.IsNullOrWhiteSpace(SelectedHit.Content))
        {
            var snippet = SelectedHit.Content.Length > 200 ? SelectedHit.Content[..200] + "…" : SelectedHit.Content;
            var prompt = $"关于文档《{SelectedHit.Source}》中的内容：\n「{snippet}」\n请帮我解释和总结。";
            AskInChatRequested?.Invoke(prompt);
        }
    }
}
