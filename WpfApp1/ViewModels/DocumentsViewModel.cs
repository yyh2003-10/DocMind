using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class DocumentsViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;
    private readonly NotificationService _notifications;

    private string? _collection;
    private int _page = 1;
    private const int PageSize = 20;
    private int _total;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private Document? _selectedDocument;
    private DocumentDetail? _detail;
    /// <summary>当前详情已加载的分块上限（每次「加载更多」递增）。</summary>
    private int _detailChunkLimit = 20;
    private const int DetailChunkStep = 30;
    private const int DetailChunkMax = 200;
    private bool _isDetailLoading;
    /// <summary>详情请求序号：切换选中时递增，用于丢弃过期响应（防竞态）。</summary>
    private int _detailLoadSeq;

    public DocumentsViewModel(IDoc2kbApiService apiService, NotificationService notifications)
    {
        _apiService = apiService;
        _notifications = notifications;
        Title = "文档库";
        Documents = new ObservableCollection<Document>();
    }

    // ===================== 导航激活自动加载 =====================

    private bool _hasLoadedOnce;

    /// <summary>切换为该页面时触发一次加载（幂等：仅首次自动加载，避免重复请求）。</summary>
    public async Task EnsureLoadedAsync()
    {
        if (_hasLoadedOnce)
        {
            return;
        }
        _hasLoadedOnce = true;
        await RefreshAsync();
    }

    /// <summary>外部数据变更（如导入完成）后使缓存失效：下次进入页面自动重新加载。</summary>
    public void InvalidateCache() => _hasLoadedOnce = false;

    /// <summary>集合名（可选，留空为全部）。</summary>
    public string? Collection
    {
        get => _collection;
        set => SetProperty(ref _collection, value);
    }

    public int Page
    {
        get => _page;
        set
        {
            if (SetProperty(ref _page, value))
            {
                OnPropertyChanged(nameof(PageInfo));
                OnPropertyChanged(nameof(CanGoPrev));
                OnPropertyChanged(nameof(CanGoNext));
            }
        }
    }

    public int Total
    {
        get => _total;
        set
        {
            if (SetProperty(ref _total, value))
            {
                OnPropertyChanged(nameof(PageInfo));
                OnPropertyChanged(nameof(CanGoNext));
            }
        }
    }

    public string PageInfo => Total == 0 ? "无文档" : $"第 {Page} 页 / 共 {Total} 个文档";

    public bool CanGoPrev => Page > 1;
    public bool CanGoNext => Page * PageSize < Total;

    public bool IsBusy
    {
        get => _isBusy;
        set
        {
            if (SetProperty(ref _isBusy, value))
            {
                RefreshCommand.NotifyCanExecuteChanged();
            }
        }
    }

    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>文档列表。</summary>
    public ObservableCollection<Document> Documents { get; }

    /// <summary>当前选中文档。</summary>
    public Document? SelectedDocument
    {
        get => _selectedDocument;
        set
        {
            if (SetProperty(ref _selectedDocument, value))
            {
                OnPropertyChanged(nameof(HasSelection));
                if (value is not null)
                {
                    _ = LoadDetailAsync(value.Id);
                }
                else
                {
                    // 取消选中时清空旧详情，避免显示错位的文档内容
                    Detail = null;
                }
            }
        }
    }

    public bool HasSelection => SelectedDocument != null;

    /// <summary>详情（含 chunks 预览）。</summary>
    public DocumentDetail? Detail
    {
        get => _detail;
        set
        {
            if (SetProperty(ref _detail, value))
            {
                OnPropertyChanged(nameof(HasDetail));
                OnPropertyChanged(nameof(HasNoDetail));
                OnPropertyChanged(nameof(ChunkInfoText));
                OnPropertyChanged(nameof(CanLoadMoreChunks));
            }
        }
    }

    public bool HasDetail => Detail != null && DetailError is null;

    /// <summary>无详情时显示空态提示或加载状态。</summary>
    public bool HasNoDetail => Detail is null || DetailError is not null;

    /// <summary>详情加载错误信息（用户可见）。加载失败后显示，成功后清除。</summary>
    public string? DetailError
    {
        get => _detailError;
        set
        {
            if (SetProperty(ref _detailError, value))
            {
                OnPropertyChanged(nameof(HasDetail));
                OnPropertyChanged(nameof(HasNoDetail));
                OnPropertyChanged(nameof(HasDetailError));
            }
        }
    }
    private string? _detailError;

    /// <summary>是否有详情加载错误（用于 UI 错误面板可见性）。</summary>
    public bool HasDetailError => DetailError is not null;

    /// <summary>分块预览计数：已显示 X / 共 N。</summary>
    public string ChunkInfoText => Detail is null
        ? string.Empty
        : $"已显示 {Detail.ChunksPreview.Count} / 共 {Detail.Document.ChunkCount} 个分块";

    /// <summary>是否还有更多分块可加载。</summary>
    public bool CanLoadMoreChunks =>
        Detail != null
        && _detailChunkLimit < Math.Min(Detail.Document.ChunkCount, DetailChunkMax)
        && !IsDetailLoading;

    /// <summary>是否正在加载更多分块。</summary>
    public bool IsDetailLoading
    {
        get => _isDetailLoading;
        private set
        {
            if (SetProperty(ref _isDetailLoading, value))
            {
                OnPropertyChanged(nameof(CanLoadMoreChunks));
            }
        }
    }

    /// <summary>分块点击 → 跳转搜索页定位（由 MainViewModel 订阅）。</summary>
    public event Action<string>? ChunkSearchRequested;

    private bool CanRefresh => !IsBusy;

    /// <summary>加载文档列表（可选集合过滤）。</summary>
    [RelayCommand(CanExecute = nameof(CanRefresh))]
    private async Task RefreshAsync()
    {
        if (!CanRefresh)
        {
            return;
        }

        IsBusy = true;
        StatusMessage = "加载中…";
        DebugLog.Info($"加载文档列表: Collection='{(string.IsNullOrWhiteSpace(Collection) ? "(全部)" : Collection.Trim())}' Page={Page}", "Documents");
        var sw = System.Diagnostics.Stopwatch.StartNew();

        try
        {
            var col = string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim();
            var resp = await _apiService.ListDocumentsAsync(collection: col, page: Page, pageSize: PageSize);

            sw.Stop();
            Documents.Clear();
            foreach (var d in resp.Documents)
            {
                Documents.Add(d);
            }
            Total = resp.Total;
            Detail = null;
            SelectedDocument = null;

            StatusMessage = $"共 {resp.Total} 个文档 · 本页 {resp.Documents.Count} 个 · 耗时 {sw.ElapsedMilliseconds}ms";
            DebugLog.Info($"文档列表加载完成: total={resp.Total} page={resp.Page}/{Total} 耗时{sw.ElapsedMilliseconds}ms", "Documents");
        }
        catch (ApiException ex)
        {
            sw.Stop();
            StatusMessage = $"API 错误：{ex.Message}";
            DebugLog.Error($"文档列表 API 错误: code={ex.Code} message={ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Documents", ex);
        }
        catch (BackendConnectionException ex)
        {
            sw.Stop();
            StatusMessage = $"后端不可达：{ex.Message}";
            DebugLog.Error($"文档列表后端不可达: {ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Documents", ex);
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"文档列表未知异常 耗时{sw.ElapsedMilliseconds}ms", "Documents", ex);
        }
        finally
        {
            IsBusy = false;
        }
    }

    /// <summary>加载选中文档的详情（chunks 预览）。
    /// 请求序号防竞态：快速切换选中时，丢弃过期请求的响应，避免旧详情覆盖新选中。</summary>
    private async Task LoadDetailAsync(string id)
    {
        var seq = ++_detailLoadSeq;
        _detailChunkLimit = 20; // 新文档从默认上限重新开始
        DetailError = null; // 清除上次错误
        try
        {
            var detail = await _apiService.GetDocumentAsync(id, chunks: _detailChunkLimit, chunkContentLength: 300);
            if (seq != _detailLoadSeq)
            {
                return; // 期间用户已切换选中，丢弃过期响应
            }
            Detail = detail;
            DetailError = null; // 成功则清除错误
            DebugLog.Info($"文档详情加载完成: id={id} chunksPreview={detail.ChunksPreview.Count}", "Documents");
        }
        catch (ApiException ex)
        {
            if (seq == _detailLoadSeq)
            {
                DetailError = $"详情加载失败：{ex.Message}";
                DebugLog.Error($"文档详情 API 错误: id={id} code={ex.Code} message={ex.Message}", "Documents", ex);
            }
        }
        catch (BackendConnectionException ex)
        {
            if (seq == _detailLoadSeq)
            {
                DetailError = $"后端不可达：{ex.Message}";
                DebugLog.Error($"文档详情后端不可达: id={id} {ex.Message}", "Documents", ex);
            }
        }
        catch (Exception ex)
        {
            if (seq == _detailLoadSeq)
            {
                DetailError = $"详情加载失败：{ex.Message}";
                DebugLog.Error($"文档详情加载失败: id={id} {ex.Message}", "Documents", ex);
            }
        }
    }

    /// <summary>加载更多分块（递增加载，上限 DetailChunkMax 防一次拉取过多）。</summary>
    [RelayCommand]
    private async Task LoadMoreChunksAsync()
    {
        if (Detail is null || SelectedDocument is null || IsDetailLoading)
        {
            return;
        }

        IsDetailLoading = true;
        try
        {
            var seq = ++_detailLoadSeq;
            var target = Math.Min(_detailChunkLimit + DetailChunkStep, DetailChunkMax);
            var detail = await _apiService.GetDocumentAsync(SelectedDocument.Id, chunks: target, chunkContentLength: 300);
            if (seq != _detailLoadSeq)
            {
                return; // 期间用户已切换选中，丢弃过期响应
            }
            Detail = detail;
            _detailChunkLimit = target;
            DebugLog.Info($"加载更多分块完成: id={SelectedDocument.Id} chunks={detail.ChunksPreview.Count}", "Documents");
        }
        catch (Exception ex)
        {
            DetailError = $"加载更多分块失败：{ex.Message}";
            DebugLog.Error($"加载更多分块失败: {ex.Message}", "Documents", ex);
        }
        finally
        {
            IsDetailLoading = false;
        }
    }

    /// <summary>用分块标题/内容发起搜索定位（跳转到搜索页）。</summary>
    [RelayCommand]
    private void SearchChunk(Chunk? chunk)
    {
        if (chunk is null)
        {
            return;
        }

        var query = !string.IsNullOrWhiteSpace(chunk.Heading)
            ? chunk.Heading.Trim()
            : chunk.Content.Trim();
        if (string.IsNullOrWhiteSpace(query))
        {
            return;
        }
        if (query.Length > 80)
        {
            query = query[..80];
        }

        DebugLog.Info($"分块搜索定位: chunkIndex={chunk.ChunkIndex} query='{query}'", "Documents");
        ChunkSearchRequested?.Invoke(query);
    }

    /// <summary>删除选中文档（确认后）。</summary>
    [RelayCommand]
    private async Task DeleteAsync()
    {
        if (SelectedDocument is null)
        {
            return;
        }

        var doc = SelectedDocument;
        var confirm = System.Windows.MessageBox.Show(
            $"确定删除文档「{doc.Source}」吗？\n将同时删除其 {doc.ChunkCount} 个分块与向量。",
            "确认删除",
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Warning);
        if (confirm != System.Windows.MessageBoxResult.Yes)
        {
            return;
        }

        IsBusy = true;
        StatusMessage = "删除中…";
        DebugLog.Info($"删除文档: id={doc.Id} source='{doc.Source}'", "Documents");
        var sw = System.Diagnostics.Stopwatch.StartNew();

        try
        {
            var col = string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim();
            var resp = await _apiService.DeleteDocumentAsync(doc.Id, col);

            sw.Stop();
            if (resp.Deleted)
            {
                Documents.Remove(doc);
                Total = Math.Max(0, Total - 1);
                SelectedDocument = null;
                Detail = null;
                StatusMessage = $"已删除「{doc.Source}」（移除 {resp.DeletedChunks} 个分块）";
                _notifications.Success($"已删除 {doc.Source}");
                DebugLog.Info($"删除成功: id={doc.Id} deletedChunks={resp.DeletedChunks} 耗时{sw.ElapsedMilliseconds}ms", "Documents");
            }
            else
            {
                StatusMessage = $"删除失败：后端返回 {resp.Status}";
                _notifications.Error($"删除失败：{resp.Status}");
                DebugLog.Error($"删除失败: id={doc.Id} status={resp.Status} 耗时{sw.ElapsedMilliseconds}ms", "Documents");
            }
        }
        catch (ApiException ex)
        {
            sw.Stop();
            StatusMessage = $"API 错误：{ex.Message}";
            DebugLog.Error($"删除 API 错误: code={ex.Code} message={ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Documents", ex);
        }
        catch (BackendConnectionException ex)
        {
            sw.Stop();
            StatusMessage = $"后端不可达：{ex.Message}";
            DebugLog.Error($"删除后端不可达: {ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Documents", ex);
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"删除未知异常 耗时{sw.ElapsedMilliseconds}ms", "Documents", ex);
        }
        finally
        {
            IsBusy = false;
        }
    }

    [RelayCommand]
    private void PrevPage()
    {
        if (CanGoPrev)
        {
            Page -= 1;
            _ = RefreshAsync();
        }
    }

    [RelayCommand]
    private void NextPage()
    {
        if (CanGoNext)
        {
            Page += 1;
            _ = RefreshAsync();
        }
    }

    // ===================== 重建索引 =====================

    private bool _isReindexing;
    private string? _reindexStatus;
    private CancellationTokenSource? _reindexCts;

    /// <summary>是否正在重建索引。</summary>
    public bool IsReindexing
    {
        get => _isReindexing;
        set
        {
            if (SetProperty(ref _isReindexing, value))
            {
                ReindexCommand.NotifyCanExecuteChanged();
                OnPropertyChanged(nameof(ReindexProgressPercent));
            }
        }
    }

    /// <summary>重建索引进度百分比（0-100）。</summary>
    public int ReindexProgressPercent
    {
        get
        {
            if (!IsReindexing || _reindexTotal <= 0)
            {
                return 0;
            }
            return Math.Clamp((int)(_reindexProcessed * 100.0 / _reindexTotal), 0, 100);
        }
    }

    public string? ReindexStatus
    {
        get => _reindexStatus;
        set => SetProperty(ref _reindexStatus, value);
    }

    private int _reindexProcessed;
    private int _reindexTotal;

    private bool CanReindex => !IsReindexing && !IsBusy;

    /// <summary>取消进行中的重建索引轮询（窗口关闭时由 MainViewModel 统一调用）。</summary>
    public void CancelReindexPolling() => _reindexCts?.Cancel();

    /// <summary>重建当前集合（或全部）的向量索引，用后端任务轮询进度。</summary>
    [RelayCommand(CanExecute = nameof(CanReindex))]
    private async Task ReindexAsync()
    {
        if (!CanReindex)
        {
            return;
        }

        var col = string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim();
        var confirm = System.Windows.MessageBox.Show(
            $"确定重建索引吗？\n集合：{(col ?? "(全部)")}\n将重新嵌入该集合内所有分块。",
            "确认重建索引",
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Question);
        if (confirm != System.Windows.MessageBoxResult.Yes)
        {
            return;
        }

        IsReindexing = true;
        _reindexProcessed = 0;
        _reindexTotal = 0;
        _reindexCts = new CancellationTokenSource();
        ReindexStatus = "提交重建索引任务…";
        DebugLog.Info($"提交重建索引: Collection='{(col ?? "(全部)")}'", "Documents");

        try
        {
            var job = await _apiService.ReindexAsync(new ReindexRequest { Collection = col });
            DebugLog.Info($"重建索引任务已创建: jobId={job.JobId} status={job.Status}", "Documents");

            // 轮询直到完成：progress 0.0-1.0 → 百分比
            var final = await _apiService.PollJobUntilDoneAsync(
                job.JobId,
                progress: new Progress<JobStatus>(j =>
                {
                    _reindexProcessed = j.Processed;
                    _reindexTotal = j.Total;
                    OnPropertyChanged(nameof(ReindexProgressPercent));
                    ReindexStatus = j.Status.Equals("running", StringComparison.OrdinalIgnoreCase)
                        ? $"重建中 {j.Processed}/{j.Total} 分块"
                        : $"任务状态：{j.Status}";
                }),
                pollInterval: TimeSpan.FromSeconds(1),
                ct: _reindexCts.Token);

            if (final.Status.Equals("failed", StringComparison.OrdinalIgnoreCase))
            {
                StatusMessage = $"重建索引失败：{final.Error ?? "未知原因"}";
                _notifications.Error($"重建索引失败：{final.Error ?? "未知原因"}");
                DebugLog.Error($"重建索引失败: jobId={final.JobId} error={final.Error}", "Documents");
            }
            else
            {
                StatusMessage = $"重建索引完成：{final.Processed}/{final.Total} 分块";
                _notifications.Success($"重建索引完成（{final.Processed}/{final.Total} 分块）");
                DebugLog.Info($"重建索引完成: processed={final.Processed} total={final.Total}", "Documents");
            }
        }
        catch (OperationCanceledException) when (_reindexCts?.IsCancellationRequested == true)
        {
            StatusMessage = "已取消重建索引（窗口关闭/手动取消）";
            DebugLog.Info("重建索引轮询已取消", "Documents");
        }
        catch (ApiException ex)
        {
            StatusMessage = $"API 错误：{ex.Message}";
            DebugLog.Error($"重建索引 API 错误: code={ex.Code} message={ex.Message}", "Documents", ex);
        }
        catch (BackendConnectionException ex)
        {
            StatusMessage = $"后端不可达：{ex.Message}";
            DebugLog.Error($"重建索引后端不可达: {ex.Message}", "Documents", ex);
        }
        catch (Exception ex)
        {
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"重建索引未知异常", "Documents", ex);
        }
        finally
        {
            IsReindexing = false;
            ReindexStatus = null;
            _reindexCts?.Dispose();
            _reindexCts = null;
            // 重建后刷新列表（chunk 数不变，但保持数据新鲜）
            _ = RefreshAsync();
        }
    }
}
