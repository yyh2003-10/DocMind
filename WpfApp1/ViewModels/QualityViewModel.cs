using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class QualityViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;

    private string? _collection;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private QualityReport? _report;
    private Stats? _stats;

    public QualityViewModel(IDoc2kbApiService apiService)
    {
        _apiService = apiService;
        Title = "质量看板";
        Warnings = new ObservableCollection<string>();
        Collections = new ObservableCollection<CollectionStats>();
    }

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

    /// <summary>各集合文档/chunk 分布（用于图表/列表展示）。</summary>
    public ObservableCollection<CollectionStats> Collections { get; }

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

            sw.Stop();
            foreach (var w in Report.Warnings)
            {
                Warnings.Add(w);
            }
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
}
