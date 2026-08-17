using System.Collections.ObjectModel;
using System.IO;
using System.Text;
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
    private bool _force;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private int _progressPercent;
    private CancellationTokenSource? _importCts;

    /// <summary>导入流程结束（成功/失败/取消）时触发，供其他页面联动刷新（如文档库）。</summary>
    public event Action? ImportCompleted;

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
                UpdateSelectedPathInfo();
            }
        }
    }

    /// <summary>是否已选择路径（控制预览面板显示）。</summary>
    public bool HasSelectedPath => !string.IsNullOrWhiteSpace(SelectedPath);

    /// <summary>选中项摘要：名称 · 类型 · 大小。</summary>
    public string SelectedPathSummary => BuildPathSummary();

    /// <summary>选中项预览：文本类文件显示开头内容，其他显示提示。</summary>
    public string SelectedPathPreview => BuildPathPreview();

    private static readonly string[] PreviewTextExtensions = new[]
    {
        ".md", ".txt", ".json", ".html", ".htm", ".csv", ".xml", ".log",
        ".yaml", ".yml", ".py", ".cs", ".c", ".cpp", ".h", ".java", ".js", ".ts",
    };

    private void UpdateSelectedPathInfo()
    {
        OnPropertyChanged(nameof(HasSelectedPath));
        OnPropertyChanged(nameof(SelectedPathSummary));
        OnPropertyChanged(nameof(SelectedPathPreview));
    }

    private string BuildPathSummary()
    {
        var path = SelectedPath?.Trim() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(path))
        {
            return string.Empty;
        }

        if (Directory.Exists(path))
        {
            // 目录：统计文件数（上限 500，避免大目录卡 UI）+ 总大小
            int count = 0;
            long total = 0;
            try
            {
                var opt = Recursive ? SearchOption.AllDirectories : SearchOption.TopDirectoryOnly;
                foreach (var f in Directory.EnumerateFiles(path, "*", opt))
                {
                    if (++count > 500)
                    {
                        break;
                    }
                    try { total += new FileInfo(f).Length; }
                    catch { /* 忽略无法访问的文件 */ }
                }
            }
            catch { /* 目录不可读时忽略 */ }

            var name = Path.GetFileName(path.TrimEnd('\\', '/'));
            var countText = count > 500 ? "500+ 个" : $"{count} 个";
            var recText = Recursive ? "（递归）" : "";
            return $"📁 {name} — 文件夹{recText} · {countText}文件 · {FormatSize(total)}";
        }

        if (File.Exists(path))
        {
            try
            {
                var fi = new FileInfo(path);
                return $"📄 {fi.Name} — {FormatSize(fi.Length)} · 修改于 {fi.LastWriteTime:yyyy-MM-dd HH:mm}";
            }
            catch
            {
                return $"📄 {Path.GetFileName(path)}";
            }
        }

        return $"{Path.GetFileName(path)} — 路径不存在";
    }

    private string BuildPathPreview()
    {
        var path = SelectedPath?.Trim() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
        {
            return string.Empty;
        }

        var ext = Path.GetExtension(path).ToLowerInvariant();
        if (!PreviewTextExtensions.Contains(ext))
        {
            return "非文本格式：可用「格式转换」预览内容，或直接导入后查看分块。";
        }

        try
        {
            using var reader = new StreamReader(path, Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
            var buf = new char[2000];
            var read = reader.Read(buf, 0, buf.Length);
            var text = new string(buf, 0, read);
            return text.Length > 0 && read == buf.Length
                ? text + "\n…（预览截断）"
                : text;
        }
        catch (Exception ex)
        {
            return $"无法读取预览：{ex.Message}";
        }
    }

    private static string FormatSize(long bytes)
        => bytes >= 1L << 30 ? $"{bytes / (double)(1L << 30):F2} GB"
         : bytes >= 1L << 20 ? $"{bytes / (double)(1L << 20):F1} MB"
         : bytes >= 1L << 10 ? $"{bytes / (double)(1L << 10):F0} KB"
         : $"{bytes} B";

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

    /// <summary>强制重新摄入已存在的文件（覆盖）。</summary>
    public bool Force
    {
        get => _force;
        set => SetProperty(ref _force, value);
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
                CancelImportCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>底部状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>进度百分比（0-100），由异步 job 轮询推送。</summary>
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

    private bool CanCancel => IsBusy && _importCts is { IsCancellationRequested: false };

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

    /// <summary>执行导入：异步 job + 轮询真实进度，可从任务页取消。</summary>
    [RelayCommand(CanExecute = nameof(CanImport))]
    private async Task ImportAsync()
    {
        if (!CanImport)
        {
            return;
        }

        _importCts = new CancellationTokenSource();
        IsBusy = true;
        StatusMessage = "导入中…";
        Results.Clear();
        Skipped.Clear();
        Failed.Clear();
        ProgressPercent = 0;

        DebugLog.Info($"开始导入: Path='{SelectedPath.Trim()}' Collection='{(string.IsNullOrWhiteSpace(Collection) ? "default" : Collection.Trim())}' Recursive={Recursive}", "Import");
        var sw = System.Diagnostics.Stopwatch.StartNew();

        try
        {
            // 提交异步摄入任务（POST /v1/ingest/job），后端后台线程逐文件处理，
            // 前端轮询 GET /v1/jobs/{id} 获取真实进度。
            var job = await _apiService.IngestJobAsync(
                new IngestRequest
                {
                    Path = SelectedPath.Trim(),
                    // 后端 collection 非 Optional，传 null 会 422；空时发 "default"
                    Collection = string.IsNullOrWhiteSpace(Collection) ? "default" : Collection.Trim(),
                    Recursive = Recursive,
                    Force = Force,
                },
                _importCts.Token);

            _currentJobId = job.JobId;
            DebugLog.Info($"导入任务已创建: jobId={job.JobId} status={job.Status}", "Import");

            // 轮询直到完成：progress 0.0-1.0 → 百分比
            var final = await _apiService.PollJobUntilDoneAsync(
                job.JobId,
                progress: new Progress<JobStatus>(j =>
                {
                    ProgressPercent = (int)Math.Round(j.Progress * 100);
                    StatusMessage = j.Status.Equals("running", StringComparison.OrdinalIgnoreCase)
                        ? $"导入中 {j.Processed}/{j.Total} 个文件"
                        : $"任务状态：{j.Status}";
                }),
                pollInterval: TimeSpan.FromSeconds(1),
                ct: _importCts.Token);

            sw.Stop();

            if (final.Status.Equals("failed", StringComparison.OrdinalIgnoreCase))
            {
                StatusMessage = $"导入失败：{final.Error ?? "未知原因"}";
                Failed.Add($"任务失败：{final.Error ?? "未知原因"}");
                _notifications.Error($"导入失败：{final.Error ?? "未知原因"}");
                DebugLog.Error($"导入任务失败: jobId={final.JobId} error={final.Error}", "Import");
                return;
            }

            // 后端 JobStatus.results：每个文件的最终状态（ingested / skipped / failed），
            // 由后端在任务完成时填充，前端无需二次同步请求。
            var ingested = 0;
            var skipped = 0;
            var failed = 0;
            if (final.Results is { Count: > 0 })
            {
                foreach (var r in final.Results)
                {
                    switch (r.Status)
                    {
                        case "ingested":
                            ingested++;
                            Results.Add(r);
                            break;
                        case "skipped":
                            skipped++;
                            Skipped.Add(r.Source);
                            break;
                        case "failed":
                            failed++;
                            Failed.Add($"{r.Source}：{r.Error ?? "未知原因"}");
                            break;
                    }
                }
            }
            else
            {
                // 旧后端无 results 字段（向前兼容）：用计数占位
                ingested = final.Processed;
            }

            if (final.Results is { Count: > 0 } && skipped > 0)
            {
                Skipped.Add($"已跳过 {skipped} 个重复文件");
            }

            ProgressPercent = 100;
            StatusMessage = ingested > 0
                ? $"完成：导入 {ingested} · 跳过 {skipped} · 失败 {failed}"
                : "完成：无新增文档（全部跳过或失败）";

            DebugLog.Info(
                $"导入完成: ingested={ingested} skipped={skipped} failed={failed} " +
                $"totalDocuments={final.Processed} 耗时{sw.ElapsedMilliseconds}ms",
                "Import");
            foreach (var r in final.Results)
            {
                DebugLog.Info(
                    $"  文档: source='{r.Source}' collection='{r.Collection}' format='{r.Format}' " +
                    $"size={r.SizeBytes}B chunks={r.ChunkCount} status='{r.Status}' docId='{r.DocumentId}'",
                    "Import");
            }

            if (ingested > 0)
                _notifications.Success($"成功导入 {ingested} 个文档");
            if (failed > 0)
                _notifications.Warning($"{failed} 个文档导入失败");
        }
        catch (OperationCanceledException) when (_importCts.IsCancellationRequested)
        {
            sw.Stop();
            StatusMessage = "已取消导入（后台可能仍在处理未完成文件）";
            _notifications.Info("导入已取消");
            DebugLog.Info($"导入已取消，耗时{sw.ElapsedMilliseconds}ms", "Import");
        }
        catch (ApiException ex)
        {
            sw.Stop();
            StatusMessage = ex.Code == "TIMEOUT"
                ? "导入超时：后端处理时间过长（OCR/嵌入耗时任务），已自动取消本次请求，可稍后重试或到「日志」页查看后端进度"
                : $"API 错误：{ex.Message}";
            DebugLog.Error($"导入 API 错误: code={ex.Code} message={ex.Message} detail={ex.Detail} 耗时{sw.ElapsedMilliseconds}ms", "Import", ex);
        }
        catch (BackendConnectionException ex)
        {
            sw.Stop();
            StatusMessage = $"后端不可达：{ex.Message}";
            DebugLog.Error($"导入后端不可达: {ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Import", ex);
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"导入未知异常 耗时{sw.ElapsedMilliseconds}ms", "Import", ex);
        }
        finally
        {
            IsBusy = false;
            _importCts?.Dispose();
            _importCts = null;
            DebugLog.Info($"导入流程结束，总耗时{sw.ElapsedMilliseconds}ms", "Import");
            // 无论成败都通知联动方（可能部分文件已成功写入库）
            ImportCompleted?.Invoke();
        }
    }

    private string? _currentJobId;

    /// <summary>取消正在进行的导入（停止前端轮询，并向后端发送取消任务请求）。</summary>
    [RelayCommand(CanExecute = nameof(CanCancel))]
    private async Task CancelImport()
    {
        StatusMessage = "正在取消导入并通知后端…";
        _importCts?.Cancel();
        if (!string.IsNullOrWhiteSpace(_currentJobId))
        {
            try
            {
                await _apiService.CancelJobAsync(_currentJobId);
                DebugLog.Info($"已向后端发送取消任务请求: jobId={_currentJobId}", "Import");
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"向后端发送取消任务请求失败: {ex.Message}", "Import");
            }
        }
    }

    /// <summary>清空当前结果与状态。</summary>
    [RelayCommand]
    private void Reset()
    {
        SelectedPath = string.Empty;
        Collection = null;
        Recursive = false;
        Force = false;
        Results.Clear();
        Skipped.Clear();
        Failed.Clear();
        ProgressPercent = 0;
        StatusMessage = "就绪";
    }
}