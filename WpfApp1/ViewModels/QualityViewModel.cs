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

        try
        {
            var col = string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim();
            Report = await _apiService.GetQualityAsync(col);
            Stats = await _apiService.GetStatsAsync(null);

            foreach (var w in Report.Warnings)
            {
                Warnings.Add(w);
            }
            foreach (var c in Stats.Collections)
            {
                Collections.Add(c);
            }

            var generated = Stats.GeneratedAt?.LocalDateTime;
            StatusMessage = $"更新于 {(generated is null ? "—" : generated.Value.ToString("G"))}";
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
}
