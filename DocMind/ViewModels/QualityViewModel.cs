using System.Collections.ObjectModel;
using System.Text;
using System.Text.Json;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;
using LiveChartsCore;
using LiveChartsCore.SkiaSharpView;
using LiveChartsCore.SkiaSharpView.Painting;
using SkiaSharp;

namespace DocMind.ViewModels;

public partial class QualityViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;
    private readonly NotificationService? _notifications;
    private readonly AppSettings? _appSettings;

    /// <summary>后端不可达 → 全局离线横幅（FC-03/08）。</summary>
    public event Action? BackendUnreachable;

    /// <summary>LLM 未配置 → 跳转设置页。</summary>
    public event Action? NavigateToSettingsRequested;

    [RelayCommand]
    private void NavigateToSettings() => NavigateToSettingsRequested?.Invoke();

    /// <summary>AI 整理事前门禁（FC-06 类）。settings 为空时不阻断（兼容单测）。</summary>
    public bool IsLlmConfigured
    {
        get
        {
            if (_appSettings is null)
            {
                return true;
            }
            var provider = _appSettings.LlmProvider?.Trim() ?? "";
            if (provider.Length > 0 && !string.Equals(provider, "none", StringComparison.OrdinalIgnoreCase)
                && (string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase)
                    || !string.IsNullOrWhiteSpace(_appSettings.LlmApiKey)))
            {
                return true;
            }
            return _appSettings.LlmProfiles?.Any(p => p is { IsEnabled: true }
                && (string.Equals(p.Provider, "ollama", StringComparison.OrdinalIgnoreCase)
                    || !string.IsNullOrWhiteSpace(p.ApiKey))) == true;
        }
    }

    public string LlmConfigHint =>
        "尚未配置大模型：AI 整理需要 LLM，请到【设置 → 大模型对话】完成配置。\n"
        + "完全离线可选择 Ollama（本机推理、无需 API Key）。";

    public void NotifyLlmGateChanged()
    {
        OnPropertyChanged(nameof(IsLlmConfigured));
        PreviewCurateCommand.NotifyCanExecuteChanged();
        ExecuteCurateCommand.NotifyCanExecuteChanged();
    }

    private string? _collection;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private QualityReport? _report;
    private Stats? _stats;
    private bool _isCurating;
    private int _curateProgressPercent;
    private string _curateStatus = "";
    private string _curateSummary = "";
    private bool _hasPreviewResult;
    private CancellationTokenSource? _curateCts;

    public QualityViewModel(
        IDoc2kbApiService apiService,
        NotificationService? notifications = null,
        AppSettings? appSettings = null)
    {
        _apiService = apiService;
        _notifications = notifications;
        _appSettings = appSettings;
        Title = "质量看板";
        Warnings = new ObservableCollection<string>();
        Collections = new ObservableCollection<CollectionStats>();
    }

    // ===================== 导航激活自动加载 =====================

    private bool _hasLoadedOnce;
    private bool _refreshSucceeded;

    /// <summary>切换为该页面时触发一次加载；失败不缓存，下一次进入会自动重试。</summary>
    public async Task EnsureLoadedAsync()
    {
        if (_hasLoadedOnce)
        {
            return;
        }
        _refreshSucceeded = false;
        await RefreshAsync();
        _hasLoadedOnce = _refreshSucceeded;
    }

    /// <summary>使质量看板缓存失效，下次进入或导入完成后强制刷新数据。</summary>
    public void InvalidateCache() => _hasLoadedOnce = false;

    /// <summary>集合名（可选，默认 default）。</summary>
    public string? Collection
    {
        get => _collection;
        set => SetProperty(ref _collection, value);
    }

    /// <summary>是否正在拉取数据。</summary>
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

    /// <summary>底部状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>质量报告（重复率 / 元数据缺失率 / 警告）。</summary>
    public QualityReport? Report
    {
        get => _report;
        set => SetProperty(ref _report, value);
    }

    /// <summary>总体统计（文档数 / chunk 数 / 各集合分布）。</summary>
    public Stats? Stats
    {
        get => _stats;
        set => SetProperty(ref _stats, value);
    }

    /// <summary>质量报告中的警告清单。</summary>
    public ObservableCollection<string> Warnings { get; }

    /// <summary>是否有质量警告。</summary>
    public bool HasWarnings => Warnings.Count > 0;

    // ── 库状态（最新 / 待同步 / 索引过期） ──

    private LibraryStatus? _libraryStatus;

    public LibraryStatus? LibraryStatus
    {
        get => _libraryStatus;
        private set
        {
            if (SetProperty(ref _libraryStatus, value))
            {
                OnPropertyChanged(nameof(LibraryStatusText));
                OnPropertyChanged(nameof(LibraryStatusBadge));
                OnPropertyChanged(nameof(HasLibraryIssues));
                OnPropertyChanged(nameof(NeedsReindex));
            }
        }
    }

    public string LibraryStatusText =>
        string.IsNullOrWhiteSpace(LibraryStatus?.Summary) ? "库状态未知" : LibraryStatus!.Summary;

    public string LibraryStatusBadge => LibraryStatus?.Status switch
    {
        "ok" => "最新",
        "empty" => "空库",
        "warn" => "有提醒",
        "reindex_needed" => "需重建索引",
        _ => "未知",
    };

    public bool HasLibraryIssues => LibraryStatus?.Issues is { Count: > 0 };
    public bool NeedsReindex => LibraryStatus?.NeedsReindex == true;

    private bool _isReindexingFromStatus;

    /// <summary>库状态提示需重建时，一键触发重建索引（确认后执行，复用 Documents 页同款 API）。</summary>
    [RelayCommand]
    private async Task ReindexFromStatusAsync()
    {
        if (_isReindexingFromStatus)
        {
            return;
        }
        var confirm = System.Windows.MessageBox.Show(
            "确定重建索引吗？\n将按当前嵌入模型重新计算全部分块向量。\n大库可能耗时较长，期间检索可能降级。",
            "确认重建索引",
            System.Windows.MessageBoxButton.YesNo,
            System.Windows.MessageBoxImage.Question);
        if (confirm != System.Windows.MessageBoxResult.Yes)
        {
            return;
        }

        _isReindexingFromStatus = true;
        try
        {
            StatusMessage = "提交重建索引任务…";
            var job = await _apiService.ReindexAsync(new ReindexRequest { Collection = null });
            DebugLog.Info($"库状态一键重建: jobId={job.JobId}", "Quality");
            var final = await _apiService.WatchJobUntilDoneAsync(job.JobId);
            if (string.Equals(final.Status, "completed", StringComparison.OrdinalIgnoreCase))
            {
                _notifications.Success($"重建索引完成（{final.Processed}/{final.Total} 分块）");
                InvalidateCache();
                await RefreshAsync();
            }
            else if (string.Equals(final.Status, "cancelled", StringComparison.OrdinalIgnoreCase))
            {
                _notifications.Info("重建索引已取消", "重建索引");
            }
            else
            {
                _notifications.Error($"重建索引失败：{final.Error ?? final.Status}");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"重建索引失败：{ex.Message}";
            _notifications.Error($"重建索引失败：{ex.Message}");
            DebugLog.Error($"库状态一键重建失败: {ex.Message}", "Quality", ex);
        }
        finally
        {
            _isReindexingFromStatus = false;
        }
    }

    /// <summary>各集合文档/chunk 分布（用于图表/列表展示）。</summary>
    public ObservableCollection<CollectionStats> Collections { get; }

    /// <summary>格式分布饼图系列。</summary>
    public ObservableCollection<ISeries> FormatSeries { get; } = [];

    /// <summary>集合文档数柱状图系列。</summary>
    public ObservableCollection<ISeries> CollectionSeries { get; } = [];

    private void UpdateChartSeries()
    {
        FormatSeries.Clear();
        CollectionSeries.Clear();

        if (Report?.FormatDistribution is { Count: > 0 })
        {
            var colors = new[] { SKColors.DodgerBlue, SKColors.OrangeRed, SKColors.MediumSeaGreen,
                                 SKColors.Gold, SKColors.MediumPurple, SKColors.Teal,
                                 SKColors.Coral, SKColors.SkyBlue };
            var i = 0;
            foreach (var (fmt, count) in Report.FormatDistribution.OrderByDescending(kv => kv.Value))
            {
                FormatSeries.Add(new PieSeries<int>
                {
                    Values = [count],
                    Name = string.IsNullOrEmpty(fmt) ? "(未知)" : fmt,
                    Fill = new SolidColorPaint(colors[i % colors.Length]),
                    DataLabelsSize = 12,
                    DataLabelsPosition = LiveChartsCore.Measure.PolarLabelsPosition.Outer,
                });
                i++;
            }
        }

        foreach (var col in Collections)
        {
            CollectionSeries.Add(new ColumnSeries<int>
            {
                Values = [col.Documents],
                Name = col.Name,
            });
        }
    }

    private bool CanRefresh => !IsBusy;

    /// <summary>刷新质量报告 + 总体统计。</summary>
    [RelayCommand(CanExecute = nameof(CanRefresh))]
    private async Task RefreshAsync()
    {
        if (!CanRefresh)
        {
            return;
        }

        IsBusy = true;
        _refreshSucceeded = false;
        StatusMessage = "拉取中…";
        Warnings.Clear();
        Collections.Clear();

        DebugLog.Info($"开始拉取质量报告: Collection='{(string.IsNullOrWhiteSpace(Collection) ? "(全部)" : Collection.Trim())}'", "Quality");
        var sw = System.Diagnostics.Stopwatch.StartNew();

        try
        {
            var col = string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim();
            Report = await _apiService.GetQualityAsync(col);
            Stats = await _apiService.GetStatsAsync(col);
            try
            {
                LibraryStatus = await _apiService.GetLibraryStatusAsync();
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"库状态拉取失败（不影响质量报告）: {ex.Message}", "Quality");
                LibraryStatus = null;
            }

            sw.Stop();
            foreach (var w in Report.Warnings)
            {
                Warnings.Add(w);
            }
            OnPropertyChanged(nameof(HasWarnings));
            // 后端 Stats.collections 是 dict[str, [doc_count, chunk_count, size_bytes]]；
            // 网络边界反序列化数据，数组长度可能 <3（脏数据/版本差异），先校验再取下标。
            foreach (var kv in Stats.Collections)
            {
                var v = kv.Value;
                Collections.Add(new CollectionStats
                {
                    Name = kv.Key,
                    Documents = v.Length > 0 ? v[0] : 0,
                    Chunks = v.Length > 1 ? v[1] : 0,
                    SizeBytes = v.Length > 2 ? v[2] : 0,
                });
            }

            StatusMessage = "已更新";
            _refreshSucceeded = true;
            UpdateChartSeries();
            DebugLog.Info(
                $"质量报告拉取完成: collections={Stats.Collections.Count} " +
                $"totalDocuments={Stats.TotalDocuments} totalChunks={Stats.TotalChunks} warnings={Report.Warnings.Count} 耗时{sw.ElapsedMilliseconds}ms",
                "Quality");
        }
        catch (ApiException ex)
        {
            sw.Stop();
            StatusMessage = $"API 错误：{ex.Message}";
            DebugLog.Error($"质量报告 API 错误: code={ex.Code} message={ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Quality", ex);
        }
        catch (BackendConnectionException ex)
        {
            BackendUnreachable?.Invoke();
            sw.Stop();
            StatusMessage = $"后端不可达：{ex.Message}";
            DebugLog.Error($"质量报告后端不可达: {ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Quality", ex);
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"质量报告未知异常 耗时{sw.ElapsedMilliseconds}ms", "Quality", ex);
        }
        finally
        {
            IsBusy = false;
            DebugLog.Info($"质量报告流程结束，总耗时{sw.ElapsedMilliseconds}ms", "Quality");
        }
    }

    // ===================== AI 知识库整理（curate）=====================

    /// <summary>默认整理动作：打标签/摘要、归类、语义去重、归纳合并（不含 extract，避免意外实体抽取）。</summary>
    private static readonly string[] CurateActions = ["enrich", "categorize", "dedup", "consolidate"];

    /// <summary>是否正在跑 AI 整理任务。</summary>
    public bool IsCurating
    {
        get => _isCurating;
        set
        {
            if (SetProperty(ref _isCurating, value))
            {
                PreviewCurateCommand.NotifyCanExecuteChanged();
                ExecuteCurateCommand.NotifyCanExecuteChanged();
                OnPropertyChanged(nameof(CurateProgressPercent));
            }
        }
    }

    /// <summary>整理任务进度（0-100）。</summary>
    public int CurateProgressPercent
    {
        get => IsCurating && _curateProgressPercent > 0 ? _curateProgressPercent : 0;
        set => SetProperty(ref _curateProgressPercent, value);
    }

    /// <summary>整理任务状态文本（提交/进度/完成/失败）。</summary>
    public string CurateStatus
    {
        get => _curateStatus;
        set => SetProperty(ref _curateStatus, value);
    }

    /// <summary>整理报告摘要（各项动作计数）。</summary>
    public string CurateSummary
    {
        get => _curateSummary;
        set
        {
            if (SetProperty(ref _curateSummary, value))
            {
                OnPropertyChanged(nameof(HasCurateSummary));
            }
        }
    }

    public bool HasCurateSummary => !string.IsNullOrWhiteSpace(CurateSummary);

    private bool CanRunCurate => !IsCurating && IsLlmConfigured;

    /// <summary>执行按钮（dry_run=false）需先完成一次只读预览（dedup/consolidate 有损，先确认再执行）。</summary>
    private bool CanRunCurateExecute => !IsCurating && _hasPreviewResult && IsLlmConfigured;

    /// <summary>AI 整理只读预览（dry_run=true，零写入）：先看整理方案再决定是否执行。</summary>
    [RelayCommand(CanExecute = nameof(CanRunCurate))]
    private Task PreviewCurateAsync() => RunCurateAsync(dryRun: true);

    /// <summary>执行 AI 整理（dry_run=false）：预览确认后的实际落地。dedup/consolidate 有损失。</summary>
    [RelayCommand(CanExecute = nameof(CanRunCurateExecute))]
    private Task ExecuteCurateAsync() => RunCurateAsync(dryRun: false);

    /// <summary>页面离开时中止轮询（由外部调用；任务本身不强制取消，后端 job 继续或自然收敛）。</summary>
    public void CancelCuratePolling() => _curateCts?.Cancel();

    private async Task RunCurateAsync(bool dryRun)
    {
        if (IsCurating)
        {
            return;
        }
        if (!IsLlmConfigured)
        {
            CurateStatus = LlmConfigHint;
            _notifications?.Warning(LlmConfigHint, "需要配置大模型");
            NavigateToSettingsRequested?.Invoke();
            PreviewCurateCommand.NotifyCanExecuteChanged();
            ExecuteCurateCommand.NotifyCanExecuteChanged();
            return;
        }

        var col = string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim();
        IsCurating = true;
        CurateSummary = "";
        CurateStatus = dryRun ? "AI 整理预览（只读，不写入）提交中…" : "AI 整理执行提交中…";
        _curateCts = new CancellationTokenSource();
        var mode = dryRun ? "预览" : "执行";
        DebugLog.Info($"AI 整理{mode}开始: Collection='{col ?? "(全部)"}'", "Quality");

        try
        {
            // 前端显式指定动作（排除 extract）并保持 dry_run 语义，避免后端默认动作与预期偏差
            var job = await _apiService.CurateAsync(new CurateRequest
            {
                Collection = col,
                Actions = [.. CurateActions],
                DryRun = dryRun,
            }, _curateCts.Token);
            CurateStatus = $"任务已提交（{job.JobId}），等待后端…";

            // Progress<T> 的回调是异步投递的，可能在轮询返回之后才执行；
            // 若不设闸，落后的进度回调会把下面的失败/完成终态文案覆盖成中间状态文案。
            var pollingCompleted = false;
            void ApplyProgress(JobStatus j)
            {
                if (pollingCompleted) return;
                CurateProgressPercent = (int)(j.Progress * 100);
                CurateStatus = j.Status.Equals("running", StringComparison.OrdinalIgnoreCase)
                    ? $"{mode}中 {CurateProgressPercent}%（{j.Processed}/{j.Total}）…"
                    : $"任务状态: {j.Status}";
            }

            var progress = new Progress<JobStatus>(j =>
            {
                var app = System.Windows.Application.Current;
                if (app?.Dispatcher != null && !app.Dispatcher.CheckAccess())
                {
                    app.Dispatcher.InvokeAsync(() => ApplyProgress(j));
                }
                else
                {
                    ApplyProgress(j);
                }
            });

            // 优先走 job 进度 SSE 实时流（AUD-017 接通 /v1/jobs/{id}/events）；
            // 服务内部在 SSE 不可用/中断时已自动回退到轮询，此处无需感知。
            var final = await _apiService.WatchJobUntilDoneAsync(job.JobId, progress, ct: _curateCts.Token);
            pollingCompleted = true;
            CurateProgressPercent = (int)(final.Progress * 100);

            if (final.Status.Equals("failed", StringComparison.OrdinalIgnoreCase))
            {
                CurateStatus = $"AI 整理{mode}失败: {final.Error}";
                DebugLog.Error($"AI 整理{mode}失败: {final.Error}", "Quality");
            }
            else if (final.Status.Equals("cancelled", StringComparison.OrdinalIgnoreCase)
                     || final.Status.Equals("canceled", StringComparison.OrdinalIgnoreCase))
            {
                CurateStatus = $"AI 整理{mode}已取消";
            }
            else
            {
                // 预览成功后解锁执行按钮（执行后保持，允许再次执行）
                _hasPreviewResult = true;
                CurateSummary = BuildCurateSummary(final);
                CurateStatus = dryRun
                    ? "✅ 预览完成（只读，未写入任何数据）——确认无误后可「执行」"
                    : "✅ AI 整理执行完成";
            }
            ExecuteCurateCommand.NotifyCanExecuteChanged();
        }
        catch (OperationCanceledException)
        {
            CurateStatus = $"AI 整理{mode}已取消";
        }
        catch (ApiException ex)
        {
            CurateStatus = $"API 错误：{ex.Message}";
            DebugLog.Error($"AI 整理{mode} API 错误: code={ex.Code} message={ex.Message}", "Quality", ex);
        }
        catch (BackendConnectionException ex)
        {
            BackendUnreachable?.Invoke();
            CurateStatus = $"后端不可达：{ex.Message}";
            DebugLog.Error($"AI 整理{mode}后端不可达: {ex.Message}", "Quality", ex);
        }
        catch (Exception ex)
        {
            CurateStatus = $"错误：{ex.Message}";
            DebugLog.Error($"AI 整理{mode}未知异常: {ex.Message}", "Quality", ex);
        }
        finally
        {
            IsCurating = false;
            _curateCts?.Dispose();
            _curateCts = null;
        }
    }

    /// <summary>把 curate 任务的 report（原始 JSON）压成一行摘要：各项计数 + 错误数。</summary>
    private static string BuildCurateSummary(JobStatus job)
    {
        var sb = new StringBuilder();
        var re = job.Report;
        if (re is null || re.Value.ValueKind != JsonValueKind.Object)
        {
            sb.Append("后端未返回整理报告（可能无数据可整理或动作全部跳过）。");
            return sb.ToString();
        }

        var root = re.Value;
        sb.Append(root.TryGetProperty("dry_run", out var dr) && dr.ValueKind == JsonValueKind.True
            ? "只读预览 · "
            : "已执行 · ");
        if (root.TryGetProperty("actions", out var acts) && acts.ValueKind == JsonValueKind.Array)
        {
            sb.Append("动作: ").AppendJoin(", ", acts.EnumerateArray().Select(a => a.GetString())).Append(" · ");
        }
        sb.Append("打标签/摘要 ").Append(CountOf(root, "enriched")).Append(" 篇 · ");
        sb.Append("归类 ").Append(CountOf(root, "categorized")).Append(" 篇 · ");
        sb.Append("去重 ").Append(CountOf(root, "duplicates")).Append(" 组 · ");
        sb.Append("归纳 ").Append(CountOf(root, "consolidated")).Append(" 组 · ");
        sb.Append("跳过 ").Append(CountOf(root, "skipped")).Append(" · ");
        sb.Append("错误 ").Append(CountOf(root, "errors"));
        return sb.ToString();
    }

    private static int CountOf(JsonElement root, string key)
        => root.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Array ? v.GetArrayLength() : 0;

    // ===================== 整理历史 / 检索自评估 / 推荐配置 =====================

    private string _curateRunsText = "尚未加载";
    private string _evalResultText = "";
    private string _recommendedText = "";
    private bool _isEvalRunning;

    /// <summary>最近整理运行摘要。</summary>
    public string CurateRunsText
    {
        get => _curateRunsText;
        private set => SetProperty(ref _curateRunsText, value);
    }

    /// <summary>检索自评估结果摘要。</summary>
    public string EvalResultText
    {
        get => _evalResultText;
        private set => SetProperty(ref _evalResultText, value);
    }

    /// <summary>推荐检索配置摘要。</summary>
    public string RecommendedText
    {
        get => _recommendedText;
        private set => SetProperty(ref _recommendedText, value);
    }

    public bool IsEvalRunning
    {
        get => _isEvalRunning;
        private set
        {
            if (SetProperty(ref _isEvalRunning, value))
                RunEvalCommand.NotifyCanExecuteChanged();
        }
    }

    /// <summary>加载最近整理运行记录。</summary>
    [RelayCommand]
    private async Task LoadCurateRunsAsync()
    {
        try
        {
            var resp = await _apiService.ListCurateRunsAsync(days: 7, limit: 20);
            if (resp.Items.Count == 0)
            {
                CurateRunsText = "近 7 天没有整理运行记录。";
                return;
            }
            var sb = new System.Text.StringBuilder();
            sb.Append($"近 7 天 {resp.Items.Count} 次整理：");
            foreach (var it in resp.Items.Take(5))
            {
                var kind = it.DryRun ? "预览" : "落盘";
                sb.Append($"\n· {it.StartedAt} [{kind}] {string.Join("/", it.Actions)} · 改动 {it.ChangedDocIds.Count} 篇 · 跳过 {it.SkippedCount} · 错误 {it.ErrorCount}");
                if (!string.IsNullOrWhiteSpace(it.Note)) sb.Append($"（{it.Note}）");
            }
            if (resp.Items.Count > 5) sb.Append($"\n… 共 {resp.Total} 条");
            CurateRunsText = sb.ToString();
        }
        catch (Exception ex)
        {
            CurateRunsText = $"加载失败：{ex.Message}";
        }
    }

    /// <summary>运行本库检索自评估。</summary>
    [RelayCommand(CanExecute = nameof(CanRunEval))]
    private async Task RunEvalAsync()
    {
        if (IsEvalRunning) return;
        IsEvalRunning = true;
        EvalResultText = "评估中…";
        try
        {
            var r = await _apiService.EvalLibraryAsync(sample: 20);
            var sb = new System.Text.StringBuilder();
            if (r.SelfRecallAtK is { } recall) sb.Append($"SelfRecall@k {recall:P1}");
            if (r.Mrr is { } mrr) sb.Append(string.IsNullOrEmpty(sb.ToString()) ? $"MRR {mrr:P1}" : $" · MRR {mrr:P1}");
            if (r.Sample > 0) sb.Append($" · 样本 {r.Sample}");
            if (r.Suggestions.Count > 0)
            {
                sb.Append("\n建议：");
                foreach (var s in r.Suggestions.Take(5)) sb.Append($"\n· {s}");
            }
            if (sb.Length == 0) sb.Append("评估完成，无指标返回。");
            EvalResultText = sb.ToString();
        }
        catch (Exception ex)
        {
            EvalResultText = $"评估失败：{ex.Message}";
        }
        finally
        {
            IsEvalRunning = false;
        }
    }

    private bool CanRunEval => !IsEvalRunning && !IsBusy;

    /// <summary>加载推荐检索配置预览。</summary>
    [RelayCommand]
    private async Task LoadRecommendedAsync()
    {
        try
        {
            var r = await _apiService.GetRetrievalRecommendedAsync();
            RecommendedText = (r.AlignedBefore ? "当前已对齐推荐配置。" : "当前配置与推荐不一致。")
                + (string.IsNullOrWhiteSpace(r.Description) ? "" : $"\n{r.Description}");
        }
        catch (Exception ex)
        {
            RecommendedText = $"加载失败：{ex.Message}";
        }
    }

    /// <summary>一键应用推荐检索配置。</summary>
    [RelayCommand]
    private async Task ApplyRecommendedAsync()
    {
        try
        {
            var r = await _apiService.ApplyRetrievalRecommendedAsync();
            RecommendedText = r.Applied
                ? $"已应用推荐配置。{r.Description}"
                : "推荐配置已对齐，无需修改。";
            _notifications?.Success("推荐检索配置已应用", "检索配置");
        }
        catch (Exception ex)
        {
            RecommendedText = $"应用失败：{ex.Message}";
            _notifications?.Error($"应用推荐配置失败：{ex.Message}", "检索配置");
        }
    }
}
