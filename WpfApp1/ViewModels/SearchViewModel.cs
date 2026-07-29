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
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private SearchResponse? _lastResponse;
    private SearchHit? _selectedHit;

    public SearchViewModel(IDoc2kbApiService apiService)
    {
        _apiService = apiService;
        Title = "搜索";
        Hits = new ObservableCollection<SearchHit>();
    }

    /// <summary>搜索词。</summary>
    public string Query
    {
        get => _query;
        set => SetProperty(ref _query, value);
    }

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

    /// <summary>是否正在请求中。</summary>
    public bool IsBusy
    {
        get => _isBusy;
        set
        {
            if (SetProperty(ref _isBusy, value))
            {
                SearchCommand.NotifyCanExecuteChanged();
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
        set => SetProperty(ref _lastResponse, value);
    }

    /// <summary>当前选中 hit，详情区显示。</summary>
    public SearchHit? SelectedHit
    {
        get => _selectedHit;
        set => SetProperty(ref _selectedHit, value);
    }

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

        try
        {
            var resp = await _apiService.SearchAsync(
                new SearchRequest
                {
                    Query = Query.Trim(),
                    Collection = string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim(),
                    TopK = TopK,
                });

            LastResponse = resp;
            foreach (var hit in resp.Hits)
            {
                Hits.Add(hit);
            }

            StatusMessage = resp.Total > 0
                ? $"返回 {resp.Hits.Count}/{resp.Total} 条 · 耗时 {resp.ElapsedMs:F0}ms"
                : "无匹配结果";
        }
        catch (ApiException ex)
        {
            StatusMessage = $"API 错误：{ex.Message}";
        }
        catch (BackendConnectionException ex)
        {
            StatusMessage = $"后端不可达：{ex.Message}";
        }
        catch (Exception ex)
        {
            StatusMessage = $"错误：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
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
