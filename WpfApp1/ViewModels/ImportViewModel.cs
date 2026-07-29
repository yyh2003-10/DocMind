using System.Collections.ObjectModel;
using System.IO;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class ImportViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;
    private readonly NotificationService _notifications;

    private string _selectedPath = string.Empty;
    private string? _collection;
    private bool _recursive;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private int _progressPercent;

    public ImportViewModel(IDoc2kbApiService apiService, NotificationService notifications)
    {
        _apiService = apiService;
        _notifications = notifications;
        Title = "导入";
        Results = new ObservableCollection<IngestResult>();
        Skipped = new ObservableCollection<string>();
        Failed = new ObservableCollection<string>();

        // 监听集合变化以通知 HasResults
        Results.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HasResults));
        Skipped.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HasResults));
        Failed.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HasResults));
    }

    /// <summary>是否有任何导入结果（用于切换空态/结果态显示）。</summary>
    public bool HasResults => Results.Count > 0 || Skipped.Count > 0 || Failed.Count > 0;

    /// <summary>待导入的本地路径（文件或目录）。</summary>
    public string SelectedPath
    {
        get => _selectedPath;
        set
        {
            if (SetProperty(ref _selectedPath, value))
            {
                ImportCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>目标集合名（可选，默认 default）。</summary>
    public string? Collection
    {
        get => _collection;
        set => SetProperty(ref _collection, value);
    }

    /// <summary>目录时是否递归导入。</summary>
    public bool Recursive
    {
        get => _recursive;
        set => SetProperty(ref _recursive, value);
    }

    /// <summary>是否正在处理中。</summary>
    public bool IsBusy
    {
        get => _isBusy;
        set
        {
            if (SetProperty(ref _isBusy, value))
            {
                ImportCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>底部状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>进度百分比（0-100），后端 PollJobUntilDoneAsync 推送。</summary>
    public int ProgressPercent
    {
        get => _progressPercent;
        set => SetProperty(ref _progressPercent, value);
    }

    /// <summary>已成功导入文档列表。</summary>
    public ObservableCollection<IngestResult> Results { get; }

    /// <summary>跳过的文件（重复）。</summary>
    public ObservableCollection<string> Skipped { get; }

    /// <summary>失败的文件及原因。</summary>
    public ObservableCollection<string> Failed { get; }

    private bool CanImport => !IsBusy && !string.IsNullOrWhiteSpace(SelectedPath);

    /// <summary>触发文件/目录选择对话框。</summary>
    [RelayCommand]
    private void PickPath()
    {
        // 优先选目录；用户可在弹出的 MessageBox 中切换为单文件
        var dialog = new Microsoft.Win32.OpenFolderDialog
        {
            Title = "选择要导入的文件夹（或文件）",
        };
        if (dialog.ShowDialog() == true)
        {
            SelectedPath = dialog.FolderName;
            return;
        }

        // 退到文件选择
        var fileDlg = new Microsoft.Win32.OpenFileDialog
        {
            Title = "选择要导入的文件",
            Multiselect = false,
        };
        if (fileDlg.ShowDialog() == true)
        {
            SelectedPath = fileDlg.FileName;
        }
    }

    /// <summary>执行导入。</summary>
    [RelayCommand(CanExecute = nameof(CanImport))]
    private async Task ImportAsync()
    {
        if (!CanImport)
        {
            return;
        }

        IsBusy = true;
        StatusMessage = "导入中…";
        Results.Clear();
        Skipped.Clear();
        Failed.Clear();
        ProgressPercent = 0;

        try
        {
            var resp = await _apiService.IngestAsync(
                new IngestRequest
                {
                    Path = SelectedPath.Trim(),
                    Collection = string.IsNullOrWhiteSpace(Collection) ? null : Collection.Trim(),
                    Recursive = Recursive,
                });

            foreach (var r in resp.Ingested)
            {
                Results.Add(r);
            }
            foreach (var s in resp.Skipped)
            {
                Skipped.Add(s);
            }
            foreach (var f in resp.Failed)
            {
                Failed.Add(f);
            }

            ProgressPercent = 100;
            var importCount = resp.Ingested.Count;
            var skipCount = resp.Skipped.Count;
            var failCount = resp.Failed.Count;
            StatusMessage = importCount > 0
                ? $"完成：导入 {importCount} · 跳过 {skipCount} · 失败 {failCount}"
                : "完成：无新增文档（全部跳过或失败）";

            if (importCount > 0)
                _notifications.Success($"成功导入 {importCount} 个文档");
            if (failCount > 0)
                _notifications.Warning($"{failCount} 个文档导入失败");
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

    /// <summary>清空当前结果与状态。</summary>
    [RelayCommand]
    private void Reset()
    {
        SelectedPath = string.Empty;
        Collection = null;
        Recursive = false;
        Results.Clear();
        Skipped.Clear();
        Failed.Clear();
        ProgressPercent = 0;
        StatusMessage = "就绪";
    }
}
