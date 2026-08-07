using System.Collections.ObjectModel;
using System.Windows;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class SearchViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;

    private string _query = string.Empty;
    private string? _collection;
    private int _topK = 10;
    private double? _minScore;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private SearchResponse? _lastResponse;
    private SearchHit? _selectedHit;

    public SearchViewModel(IDoc2kbApiService apiService)
    {
        _apiService = apiService;
        Title = "搜索";
        Hits = new ObservableCollection<SearchHit>();
        // 结果列表变化 → 刷新空态引导可见性
        Hits.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(ShowEmptyGuide));
            OnPropertyChanged(nameof(EmptyGuideText));
        };
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

    /// <summary>结果区空态是否可见（非忙碌且无结果）。</summary>
    public bool ShowEmptyGuide => !IsBusy && Hits.Count == 0;

    /// <summary>空态引导文案：区分"还没搜过"与"搜了没结果"。</summary>
    public string EmptyGuideText => HasQuery
        ? "没有匹配的结果。\n试试换关键词，或调低「最低相似度」；\n也可以到【导入】页确认文档已加入知识库。"
        : "输入问题开始搜索。\n搜索的是已导入文档的内容（向量 + 关键词混合检索）。\n还没导入文档？先到【导入】页添加文件。";

    /// <summary>集合名（可选）。</summary>
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
                OnPropertyChanged(nameof(HasSelectedHit));
        }
    }

    /// <summary>是否有选中的搜索结果（用于详情区可见性）。</summary>
    public bool HasSelectedHit => SelectedHit != null;

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

        DebugLog.Info($"开始搜索: Query='{Query.Trim()}' Collection='{(string.IsNullOrWhiteSpace(Collection) ? "(全部)" : Collection.Trim())}' TopK={TopK}", "Search");
        var sw = System.Diagnostics.Stopwatch.StartNew();

        try
        {
            var resp = await _apiService.SearchAsync(
                new SearchRequest
                {
                    Query = Query.Trim(),
                    Collection = string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim(),
                    TopK = TopK,
                    MinScore = MinScore,
                });

            sw.Stop();
            LastResponse = resp;
            foreach (var hit in resp.Hits)
            {
                Hits.Add(hit);
            }

            StatusMessage = resp.Total > 0
                ? $"返回 {resp.Hits.Count}/{resp.Total} 条 · 耗时 {resp.ElapsedMs:F0}ms"
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
}
