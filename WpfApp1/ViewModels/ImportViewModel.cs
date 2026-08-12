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

        DebugLog.Info($"开始导入: Path='{SelectedPath.Trim()}' Collection='{(string.IsNullOrWhiteSpace(Collection) ? "default" : Collection.Trim())}' Recursive={Recursive}", "Import");
        var sw = System.Diagnostics.Stopwatch.StartNew();

        try
        {
            var resp = await _apiService.IngestAsync(
                new IngestRequest
                {
                    Path = SelectedPath.Trim(),
                    // 后端 collection 非 Optional，传 null 会 422；空时发 "default"
                    Collection = string.IsNullOrWhiteSpace(Collection) ? "default" : Collection.Trim(),
                    Recursive = Recursive,
                    Force = Force,
                });

            sw.Stop();
            foreach (var r in resp.Ingested)
            {
                Results.Add(r);
            }

            // 后端 IngestResponse：ingested 明细 + skipped/failed 计数 + failed_details 失败明细。
            // 失败栏优先展示真实文件与原因；后端无明细时才用计数占位。
            if (resp.Skipped > 0)
            {
                Skipped.Add($"已跳过 {resp.Skipped} 个重复文件");
            }
            if (resp.FailedDetails is { Count: > 0 })
            {
                foreach (var f in resp.FailedDetails)
                {
                    Failed.Add($"{f.Source}：{f.Error ?? "未知原因"}");
                }
            }
            else if (resp.Failed > 0)
            {
                Failed.Add($"导入失败 {resp.Failed} 个文件（详见后端日志）");
            }

            ProgressPercent = 100;
            var importCount = resp.Ingested.Count;
            var skipCount = resp.Skipped;
            var failCount = resp.Failed;
            StatusMessage = importCount > 0
                ? $"完成：导入 {importCount} · 跳过 {skipCount} · 失败 {failCount}"
                : "完成：无新增文档（全部跳过或失败）";

            DebugLog.Info(
                $"导入完成: ingested={importCount} skipped={skipCount} failed={failCount} " +
                $"totalDocuments={resp.TotalDocuments} totalChunks={resp.TotalChunks} 耗时{sw.ElapsedMilliseconds}ms",
                "Import");
            foreach (var r in resp.Ingested)
            {
                DebugLog.Info(
                    $"  文档: source='{r.Source}' collection='{r.Collection}' format='{r.Format}' " +
                    $"size={r.SizeBytes}B chunks={r.ChunkCount} status='{r.Status}' docId='{r.DocumentId}'",
                    "Import");
            }

            if (importCount > 0)
                _notifications.Success($"成功导入 {importCount} 个文档");
            if (failCount > 0)
                _notifications.Warning($"{failCount} 个文档导入失败");
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
            DebugLog.Info($"导入流程结束，总耗时{sw.ElapsedMilliseconds}ms", "Import");
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
