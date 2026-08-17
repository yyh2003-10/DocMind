using System.Collections.ObjectModel;
using System.Windows;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class SearchViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;

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

    public const string AllCollectionsLabel = "(全部集合)";

    public SearchViewModel(IDoc2kbApiService apiService)
    {
        _apiService = apiService;
        Title = "搜索";
        Hits = new ObservableCollection<SearchHit>();
        AvailableCollections = new ObservableCollection<string> { AllCollectionsLabel, "default" };

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

    /// <summary>结果区空态是否可见（非忙碌且无结果）。</summary>
    public bool ShowEmptyGuide => !IsBusy && Hits.Count == 0;

    /// <summary>空态引导文案：区分"还没搜过"与"搜了没结果"。</summary>
    public string EmptyGuideText => HasQuery
        ? "没有匹配的结果。\n建议：尝试更换关键词，或调低「最低相似度」；\n也可以到【导入】页确认文档已加入知识库。"
        : "输入问题或关键词开始搜索。\nDocMind 将基于向量语义与关键词进行混合检索。\n还没导入文档？先到【导入】页添加文件。";

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
            var resp = await _apiService.SearchAsync(
                new SearchRequest
                {
                    Query = Query.Trim(),
                    Collection = targetCollection,
                    TopK = TopK,
                    MinScore = MinScore,
                });

            sw.Stop();
            LastResponse = resp;
            foreach (var hit in resp.Hits)
            {
                Hits.Add(hit);
            }

            if (Hits.Count > 0)
            {
                SelectedHit = Hits[0];
            }

            StatusMessage = resp.Total > 0
                ? $"返回 {resp.Hits.Count}/{resp.Total} 条 · 耗时 {resp.ElapsedMs:F0}ms" + (resp.Degraded ? "（嵌入不可用，仅关键词检索）" : "")
                : "无匹配结果";

            DebugLog.Info($"搜索完成: hits={resp.Hits.Count} total={resp.Total} elapsed={resp.ElapsedMs:F0}ms 本地耗时{sw.ElapsedMilliseconds}ms", "Search");
        }
        catch (ApiException ex)
        {
            sw.Stop();
            StatusMessage = $"API 错误：{ex.Message}";
            DebugLog.Error($"搜索 API 错误: code={ex.Code} message={ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Search", ex);
        }
        catch (BackendConnectionException ex)
        {
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
        StatusMessage = "就绪";
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
