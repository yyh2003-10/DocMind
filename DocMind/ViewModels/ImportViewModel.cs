using System.Collections.ObjectModel;
using System.IO;
using System.Text;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

    public partial class ImportViewModel : ViewModelBase
    {

    /// <summary>后端不可达时通知 Main 刷新全局离线横幅（FC-03/08）。</summary>
    public event Action? BackendUnreachable;

        /// <summary>下拉框中的「新建分组」哨兵项，选中后切换为内联输入新名称。</summary>
        public const string NewCollectionSentinel = "＋ 新建分组…";

        private readonly IDoc2kbApiService _apiService;
        private readonly NotificationService _notifications;
        private readonly CheckpointService? _checkpoint;

        private string _selectedPath = string.Empty;
        private string? _collection;
        private string? _selectedCollectionItem;
        private bool _isCreatingCollection;
        private string _newCollectionName = string.Empty;
        private bool _recursive;
        private bool _force;
        private bool _isBusy;
        private string _statusMessage = "就绪";
        private int _progressPercent;
        private CancellationTokenSource? _importCts;

        /// <summary>导入流程结束（成功/失败/取消）时触发，供其他页面联动刷新（如文档库）。</summary>
        public event Action? ImportCompleted;

        public ImportViewModel(IDoc2kbApiService apiService, NotificationService notifications, CheckpointService? checkpoint = null)
        {
            _apiService = apiService;
            _notifications = notifications;
            _checkpoint = checkpoint;
            Title = "导入";
            Results = new ObservableCollection<IngestResult>();
            Skipped = new ObservableCollection<string>();
            Failed = new ObservableCollection<string>();
            AvailableCollections = new ObservableCollection<string> { "default", NewCollectionSentinel };
            // CollectionChanged 订阅在三个集合属性的 setter 里完成（整体替换后对新实例生效）

            // 从后端拉取已有知识库集合，供目标分组下拉选择
            _ = LoadCollectionsAsync();
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

    /// <summary>选中项摘要：名称 · 类型 · 大小（后台异步计算，UI 线程零阻塞）。</summary>
    public string SelectedPathSummary
    {
        get => _selectedPathSummary;
        private set => SetProperty(ref _selectedPathSummary, value);
    }
    private string _selectedPathSummary = string.Empty;

    /// <summary>选中项预览：文本类文件显示开头内容，其他显示提示（后台异步计算，UI 线程零阻塞）。</summary>
    public string SelectedPathPreview
    {
        get => _selectedPathPreview;
        private set => SetProperty(ref _selectedPathPreview, value);
    }
    private string _selectedPathPreview = string.Empty;

    /// <summary>当前导入耗时统计文案。</summary>
    public string ElapsedTimeText
    {
        get => _elapsedTimeText;
        private set => SetProperty(ref _elapsedTimeText, value);
    }
    private string _elapsedTimeText = string.Empty;

    /// <summary>当前正在处理的具体文件或执行步骤。</summary>
    public string CurrentProcessingItem
    {
        get => _currentProcessingItem;
        private set => SetProperty(ref _currentProcessingItem, value);
    }
    private string _currentProcessingItem = string.Empty;

    private CancellationTokenSource? _pathInfoCts;
    private System.Windows.Threading.DispatcherTimer? _elapsedTimer;

    private static readonly string[] PreviewTextExtensions = new[]
    {
        ".md", ".txt", ".json", ".html", ".htm", ".csv", ".xml", ".log",
        ".yaml", ".yml", ".py", ".cs", ".c", ".cpp", ".h", ".java", ".js", ".ts",
    };

    private void UpdateSelectedPathInfo()
    {
        OnPropertyChanged(nameof(HasSelectedPath));

        _pathInfoCts?.Cancel();
        _pathInfoCts?.Dispose();
        _pathInfoCts = new CancellationTokenSource();
        var token = _pathInfoCts.Token;

        var path = SelectedPath?.Trim() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(path))
        {
            SelectedPathSummary = string.Empty;
            SelectedPathPreview = string.Empty;
            return;
        }

        SelectedPathSummary = "⏳ 正在读取路径信息…";
        SelectedPathPreview = "⏳ 正在分析内容预览…";

        var isRecursive = Recursive;
        _ = Task.Run(() =>
        {
            try
            {
                var summary = ComputePathSummary(path, isRecursive, token);
                var preview = ComputePathPreview(path, token);
                if (token.IsCancellationRequested) return;

                var app = System.Windows.Application.Current;
                if (app?.Dispatcher != null && !app.Dispatcher.CheckAccess())
                {
                    app.Dispatcher.InvokeAsync(() =>
                    {
                        if (token.IsCancellationRequested) return;
                        SelectedPathSummary = summary;
                        SelectedPathPreview = preview;
                    });
                }
                else
                {
                    SelectedPathSummary = summary;
                    SelectedPathPreview = preview;
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                var app = System.Windows.Application.Current;
                if (app?.Dispatcher != null && !app.Dispatcher.CheckAccess())
                {
                    app.Dispatcher.InvokeAsync(() =>
                    {
                        SelectedPathSummary = $"📄 {Path.GetFileName(path)}";
                        SelectedPathPreview = $"无法读取预览: {ex.Message}";
                    });
                }
                else
                {
                    SelectedPathSummary = $"📄 {Path.GetFileName(path)}";
                    SelectedPathPreview = $"无法读取预览: {ex.Message}";
                }
            }
        }, token);
    }

    private static string ComputePathSummary(string path, bool recursive, CancellationToken token)
    {
        if (Directory.Exists(path))
        {
            // 目录：统计文件数（上限 500，避免大目录长时间卡顿）+ 总大小
            int count = 0;
            long total = 0;
            try
            {
                var opt = recursive ? SearchOption.AllDirectories : SearchOption.TopDirectoryOnly;
                foreach (var f in Directory.EnumerateFiles(path, "*", opt))
                {
                    token.ThrowIfCancellationRequested();
                    if (++count > 500)
                    {
                        break;
                    }
                    try { total += new FileInfo(f).Length; }
                    catch { /* 忽略无法访问的文件 */ }
                }
            }
            catch (OperationCanceledException) { throw; }
            catch { /* 目录不可读时忽略 */ }

            var name = Path.GetFileName(path.TrimEnd('\\', '/'));
            var countText = count > 500 ? "500+ 个" : $"{count} 个";
            var recText = recursive ? "（递归）" : "";
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

    private static string ComputePathPreview(string path, CancellationToken token)
    {
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
            token.ThrowIfCancellationRequested();
            var text = new string(buf, 0, read);
            return text.Length > 0 && read == buf.Length
                ? text + "\n…（预览截断）"
                : text;
        }
        catch (OperationCanceledException) { throw; }
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

    /// <summary>目标分组下拉当前选中项（含「＋ 新建分组…」哨兵）。</summary>
    public string? SelectedCollectionItem
    {
        get => _selectedCollectionItem;
        set
        {
            if (SetProperty(ref _selectedCollectionItem, value))
            {
                if (value == NewCollectionSentinel)
                {
                    // 选中哨兵项：切换为内联新建输入，实际目标集合不变
                    IsCreatingCollection = true;
                    NewCollectionName = string.Empty;
                }
                else
                {
                    IsCreatingCollection = false;
                    Collection = value;
                }
            }
        }
    }

    /// <summary>可选知识库集合（后端已有集合 + 哨兵「＋ 新建分组…」）。</summary>
    public ObservableCollection<string> AvailableCollections { get; }

    /// <summary>是否处于「新建分组」内联输入状态（下拉框被替换为文本框 + 确认/取消）。</summary>
    public bool IsCreatingCollection
    {
        get => _isCreatingCollection;
        private set => SetProperty(ref _isCreatingCollection, value);
    }

    /// <summary>新建分组名称输入。</summary>
    public string NewCollectionName
    {
        get => _newCollectionName;
        set => SetProperty(ref _newCollectionName, value);
    }

    private bool CanConfirmCreateCollection
        => IsCreatingCollection && !string.IsNullOrWhiteSpace(NewCollectionName);

    /// <summary>确认新建分组：调用后端创建空集合，成功后选为新目标。</summary>
    [RelayCommand(CanExecute = nameof(CanConfirmCreateCollection))]
    private async Task ConfirmCreateCollectionAsync()
    {
        var name = NewCollectionName.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            return;
        }

        try
        {
            await _apiService.CreateCollectionAsync(name);
            StatusMessage = $"已创建知识库分组：{name}";
            DebugLog.Info($"创建知识库集合成功: {name}", "Import");
        }
        catch (Exception ex)
        {
            // 创建失败不阻塞导入：后端摄入时会自动建集合，仍加入本地列表供选择
            DebugLog.Warn($"创建知识库集合失败（导入时后端会自动创建）: {ex.Message}", "Import");
            StatusMessage = $"创建分组失败：{ex.Message}（导入时后端会自动创建该分组）";
        }

        if (!AvailableCollections.Contains(name))
        {
            // 插在哨兵项之前，保持「＋ 新建分组…」始终在末尾
            AvailableCollections.Insert(AvailableCollections.Count - 1, name);
        }

        IsCreatingCollection = false;
        Collection = name;
        SelectedCollectionItem = name;
    }

    /// <summary>取消新建分组，回到下拉选择。</summary>
    [RelayCommand]
    private void CancelCreateCollection()
    {
        IsCreatingCollection = false;
        SelectedCollectionItem = Collection;
    }

    /// <summary>从后端拉取已有集合列表填充下拉框；失败时静默保留默认项。</summary>
    private bool _isLoadingCollections;

    public async Task LoadCollectionsAsync()
    {
        // 防重入：构造时加载与导入完成后的刷新可能并发
        if (_isLoadingCollections)
        {
            return;
        }
        _isLoadingCollections = true;
        try
        {
            var stats = await _apiService.GetStatsAsync();
            if (stats?.Collections == null)
            {
                return;
            }

            var current = Collection;
            // 重建列表：已有集合（排序）+ 哨兵项
            var names = stats.Collections.Keys
                .Where(k => !string.IsNullOrWhiteSpace(k))
                .OrderBy(k => k, StringComparer.OrdinalIgnoreCase)
                .ToList();
            if (names.Count == 0)
            {
                names.Add("default");
            }
            if (!string.IsNullOrWhiteSpace(current) && !names.Contains(current, StringComparer.OrdinalIgnoreCase))
            {
                // 保留本地已选但后端尚未存在的集合（如创建失败或后端未落库）
                names.Add(current);
                names.Sort(StringComparer.OrdinalIgnoreCase);
            }

            AvailableCollections.Clear();
            foreach (var n in names)
            {
                AvailableCollections.Add(n);
            }
            AvailableCollections.Add(NewCollectionSentinel);

            // 恢复当前选择；无已选时默认选中 default，保证下拉显示与实际导入目标一致
            var selectTarget = current
                ?? (AvailableCollections.Contains("default") ? "default" : names[0]);
            SelectedCollectionItem = selectTarget;
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"加载集合列表失败: {ex.Message}", "Import");
            // 失败时保证哨兵项存在，至少可手输名称/离线选择
            if (!AvailableCollections.Contains(NewCollectionSentinel))
            {
                AvailableCollections.Add(NewCollectionSentinel);
            }
        }
        finally
        {
            _isLoadingCollections = false;
        }
    }

    /// <summary>目录时是否递归导入。</summary>
    public bool Recursive
    {
        get => _recursive;
        set
        {
            if (SetProperty(ref _recursive, value))
            {
                UpdateSelectedPathInfo();
            }
        }
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

    /// <summary>已成功导入文档列表。
    /// 支持整体替换（批量导入完成时一次换实例 = 一次 UI 刷新，避免上万文件逐条 Add 卡死 UI）。</summary>
    public ObservableCollection<IngestResult> Results
    {
        get => _results;
        private set
        {
            if (SetProperty(ref _results, value))
            {
                OnPropertyChanged(nameof(HasResults));
                _results.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HasResults));
            }
        }
    }
    private ObservableCollection<IngestResult> _results = new();

    /// <summary>跳过的文件（重复）。</summary>
    public ObservableCollection<string> Skipped
    {
        get => _skipped;
        private set
        {
            if (SetProperty(ref _skipped, value))
            {
                OnPropertyChanged(nameof(HasResults));
                _skipped.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HasResults));
            }
        }
    }
    private ObservableCollection<string> _skipped = new();

    /// <summary>失败的文件及原因。</summary>
    public ObservableCollection<string> Failed
    {
        get => _failed;
        private set
        {
            if (SetProperty(ref _failed, value))
            {
                OnPropertyChanged(nameof(HasResults));
                _failed.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HasResults));
            }
        }
    }
    private ObservableCollection<string> _failed = new();

    /// <summary>扫描件/图片导入失败若因 OCR 组件缺失，追加设置页安装指引
    /// （后端 LoaderError 文案面向 pip 用户，桌面用户需要可操作的 GUI 路径）。</summary>
    internal static string? WithOcrInstallHint(string? error)
    {
        if (string.IsNullOrEmpty(error) ||
            error.IndexOf("PaddleOCR", StringComparison.OrdinalIgnoreCase) < 0)
        {
            return error;
        }
        return $"{error}（修复指引：到【设置】页展开「切换加速方案 / 增装 OCR 加速组件」，"
            + "选择「OCR 文字识别（CPU）」一键安装，完成后重试导入）";
    }

    /// <summary>把 job 快照转成"正在做什么"的一句话：优先展示后端文件内阶段
    /// （解析/切片/嵌入/写库/AI 整理，单大文件导入时的可见进度），
    /// 无阶段信息（旧后端）时退回文件计数文案。</summary>
    internal static string DescribeStage(JobStatus j)
    {
        var file = string.IsNullOrWhiteSpace(j.CurrentFile)
            ? null
            : Path.GetFileName(j.CurrentFile);
        var stageText = j.Stage switch
        {
            "parsing" => "正在解析文档",
            "chunking" => "正在切片",
            "embedding" => j.StageProgress is { } sp
                ? $"正在向量嵌入（{(int)Math.Round(sp * 100)}%）"
                : "正在向量嵌入",
            "writing" => "正在写入向量索引",
            "curating" => "正在 AI 整理",
            _ => null,
        };
        if (stageText != null)
        {
            return file != null ? $"{stageText}：{file}" : $"{stageText}…";
        }
        return !string.IsNullOrWhiteSpace(j.CurrentFile)
            ? $"正在处理: {file} ({j.Processed}/{j.Total})"
            : (j.Total > 0 ? $"正在处理文档 ({j.Processed}/{j.Total})…" : "正在执行文档切片与向量索引…");
    }

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
        ElapsedTimeText = "已耗时: 0秒";
        CurrentProcessingItem = "正在初始化导入任务…";
        Results.Clear();
        Skipped.Clear();
        Failed.Clear();
        ProgressPercent = 0;

        var opId = $"ingest_{DateTime.Now:yyyyMMdd_HHmmss}_{Path.GetFileNameWithoutExtension(SelectedPath)}";
        DebugLog.Info($"开始导入: Path='{SelectedPath.Trim()}' Collection='{(string.IsNullOrWhiteSpace(Collection) ? "default" : Collection.Trim())}' Recursive={Recursive} opId={opId}", "Import");
        var sw = System.Diagnostics.Stopwatch.StartNew();

        _elapsedTimer?.Stop();
        _elapsedTimer = new System.Windows.Threading.DispatcherTimer
        {
            Interval = TimeSpan.FromMilliseconds(500),
        };
        _elapsedTimer.Tick += (_, _) =>
        {
            var sec = (int)sw.Elapsed.TotalSeconds;
            ElapsedTimeText = sec >= 60 ? $"已耗时: {sec / 60}分{sec % 60}秒" : $"已耗时: {sec}秒";
        };
        _elapsedTimer.Start();

        try
        {
            // 保存检查点（崩溃后可恢复）
            if (_checkpoint is { } cp)
            {
                await cp.SaveCheckpointAsync(new CheckpointState
                {
                    OperationId = opId,
                    OperationType = "ingest",
                    TotalItems = 0, // 后端返回前未知总数
                    Metadata = new()
                    {
                        ["path"] = SelectedPath.Trim(),
                        ["collection"] = string.IsNullOrWhiteSpace(Collection) ? "default" : Collection.Trim(),
                        ["recursive"] = Recursive.ToString(),
                        ["force"] = Force.ToString(),
                    },
                });
            }

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
            // Progress<T> 的回调是异步投递的，可能在轮询返回之后才执行；
            // 若不设闸，落后的进度回调会把下面的失败/完成终态文案覆盖成中间状态文案（如「任务状态：failed」盖掉真正的失败原因）。
            var pollingCompleted = false;
            void ApplyProgress(JobStatus j)
            {
                if (pollingCompleted) return;
                ProgressPercent = (int)Math.Round(j.Progress * 100);
                StatusMessage = j.Status.Equals("running", StringComparison.OrdinalIgnoreCase)
                    ? $"导入中 {j.Processed}/{j.Total} 个文件"
                    : $"任务状态：{j.Status}";
                CurrentProcessingItem = DescribeStage(j);
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

            var final = await _apiService.WatchJobUntilDoneAsync(
                job.JobId,
                progress: progress,
                ct: _importCts.Token);
            pollingCompleted = true;

            sw.Stop();

            if (final.Status.Equals("failed", StringComparison.OrdinalIgnoreCase))
            {
                StatusMessage = $"导入失败：{WithOcrInstallHint(final.Error) ?? "未知原因"}";
                Failed.Add($"任务失败：{WithOcrInstallHint(final.Error) ?? "未知原因"}");
                _notifications.Error($"导入失败：{final.Error ?? "未知原因"}");
                DebugLog.Error($"导入任务失败: jobId={final.JobId} error={final.Error}", "Import");
                return;
            }

            if (final.Status.Equals("cancelled", StringComparison.OrdinalIgnoreCase)
                || final.Status.Equals("canceled", StringComparison.OrdinalIgnoreCase))
            {
                // FC-01b：取消也要展示「前 N 篇已导入」明细
                sw.Stop();
                var cancelIngested = 0;
                if (final.Results is { Count: > 0 })
                {
                    var ingestedList = new List<IngestResult>();
                    var skippedList = new List<string>();
                    var failedList = new List<string>();
                    foreach (var r in final.Results)
                    {
                        switch (r.Status)
                        {
                            case "ingested":
                                cancelIngested++;
                                ingestedList.Add(r);
                                break;
                            case "skipped":
                                skippedList.Add(r.Source);
                                break;
                            case "failed":
                                failedList.Add($"{r.Source}：{WithOcrInstallHint(r.Error) ?? "未知原因"}");
                                break;
                        }
                    }
                    Results = new ObservableCollection<IngestResult>(ingestedList);
                    Skipped = new ObservableCollection<string>(skippedList);
                    Failed = new ObservableCollection<string>(failedList);
                }
                StatusMessage = !string.IsNullOrWhiteSpace(final.CancelNote)
                    ? final.CancelNote
                    : cancelIngested > 0
                        ? $"已取消导入：前 {cancelIngested} 篇已导入可搜索，其余未处理。"
                        : "已取消导入：尚无已完成文件入库。";
                CurrentProcessingItem = "任务已取消";
                _notifications.Info(StatusMessage, "导入已取消");
                DebugLog.Info($"导入已取消: {StatusMessage} jobId={final.JobId}", "Import");
                return;
            }

            // 后端 JobStatus.results：每个文件的最终状态（ingested / skipped / failed），
            // 由后端在任务完成时填充，前端无需二次同步请求。
            var ingested = 0;
            var skipped = 0;
            var skippedOverLimit = 0;
            var failed = 0;
            if (final.Results is { Count: > 0 })
            {
                var ingestedList = new List<IngestResult>();
                var skippedList = new List<string>();
                var failedList = new List<string>();

                foreach (var r in final.Results)
                {
                    switch (r.Status)
                    {
                        case "ingested":
                            ingested++;
                            ingestedList.Add(r);
                            break;
                        case "skipped":
                            skipped++;
                            // 后端只在超限护栏拦截时填 error；重复文件的 error 为 null
                            if (r.Error is { Length: > 0 })
                            {
                                skippedOverLimit++;
                                skippedList.Add($"{r.Source}：{r.Error}");
                            }
                            else
                            {
                                skippedList.Add(r.Source);
                            }
                            break;
                        case "failed":
                            failed++;
                            failedList.Add($"{r.Source}：{WithOcrInstallHint(r.Error) ?? "未知原因"}");
                            break;
                    }
                }

                // 整体替换而非逐条 Add：上万文件时只触发 3 次 UI 刷新，
                // 避免逐条 CollectionChanged 塞满 Dispatcher 队列导致窗口卡死。
                Results = new ObservableCollection<IngestResult>(ingestedList);
                Skipped = new ObservableCollection<string>(skippedList);
                Failed = new ObservableCollection<string>(failedList);
            }
            else
            {
                // 旧后端无 results 字段（向前兼容）：用计数占位
                ingested = final.Processed;
            }

            if (final.Results is { Count: > 0 } && skipped > 0)
            {
                // 区分两种跳过原因，避免把超限文件误报成"重复文件"
                Skipped.Add(skippedOverLimit == 0
                    ? $"已跳过 {skipped} 个重复文件"
                    : skippedOverLimit == skipped
                        ? $"已跳过 {skipped} 个文件（超过导入限制，未入库）"
                        : $"已跳过 {skipped} 个文件：{skipped - skippedOverLimit} 个重复、{skippedOverLimit} 个超限");
            }

            ProgressPercent = 100;
            StatusMessage = ingested > 0
                ? $"完成：导入 {ingested} · 跳过 {skipped} · 失败 {failed}"
                : "完成：无新增文档（全部跳过或失败）";
            CurrentProcessingItem = $"导入完成（共处理 {final.Processed} 个文档）";

            DebugLog.Info(
                $"导入完成: ingested={ingested} skipped={skipped} failed={failed} " +
                $"totalDocuments={final.Processed} 耗时{sw.ElapsedMilliseconds}ms",
                "Import");
            // 逐文件明细日志移到后台线程：DebugLog 已改为异步落盘，这里只是
            // 避免在 UI 线程为上万文件逐条构造字符串/投递 UI 通知。
            var resultsForLog = final.Results;
            _ = Task.Run(() =>
            {
                foreach (var r in resultsForLog)
                {
                    DebugLog.Info(
                        $"  文档: source='{r.Source}' collection='{r.Collection}' format='{r.Format}' " +
                        $"size={r.SizeBytes}B chunks={r.ChunkCount} status='{r.Status}' docId='{r.DocumentId}'",
                        "Import");
                }
            });

            if (ingested > 0)
                _notifications.Success($"成功导入 {ingested} 个文档");
            if (skippedOverLimit > 0)
                _notifications.Warning(
                    $"{skippedOverLimit} 个文件超过导入限制（单文件大小或单次数量上限），已跳过未入库；上限可在后端配置中调整");
            if (failed > 0)
                _notifications.Warning($"{failed} 个文档导入失败");
        }
        catch (OperationCanceledException) when (_importCts.IsCancellationRequested)
        {
            sw.Stop();
            // FC-01b：取消后尽量拉一次 job，展示「已导入 N 篇」明细
            var cancelMsg = "已取消导入";
            try
            {
                if (!string.IsNullOrEmpty(_currentJobId))
                {
                    var jobAfter = await _apiService.GetJobAsync(_currentJobId);
                    var n = jobAfter.Results?.Count(r => r.Status == "ingested") ?? 0;
                    if (!string.IsNullOrWhiteSpace(jobAfter.CancelNote))
                    {
                        cancelMsg = jobAfter.CancelNote;
                    }
                    else if (n > 0)
                    {
                        cancelMsg = $"已取消导入：前 {n} 篇已导入可搜索，其余未处理。";
                    }
                    else
                    {
                        cancelMsg = "已取消导入：尚无已完成文件入库。";
                    }
                    if (jobAfter.Results is { Count: > 0 })
                    {
                        var ingestedList = jobAfter.Results.Where(r => r.Status == "ingested").ToList();
                        var skippedList = jobAfter.Results.Where(r => r.Status == "skipped").Select(r => r.Source).ToList();
                        var failedList = jobAfter.Results.Where(r => r.Status == "failed")
                            .Select(r => $"{r.Source}：{WithOcrInstallHint(r.Error) ?? "未知原因"}").ToList();
                        Results = new ObservableCollection<IngestResult>(ingestedList);
                        Skipped = new ObservableCollection<string>(skippedList);
                        Failed = new ObservableCollection<string>(failedList);
                    }
                }
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"取消后拉取 job 明细失败: {ex.Message}", "Import");
                cancelMsg = "已取消导入（明细暂不可用，可稍后在文档库确认）";
            }
            StatusMessage = cancelMsg;
            CurrentProcessingItem = "任务已取消";
            _notifications.Info(cancelMsg);
            DebugLog.Info($"导入已取消，耗时{sw.ElapsedMilliseconds}ms msg={cancelMsg}", "Import");
        }
        catch (ApiException ex)
        {
            sw.Stop();
            StatusMessage = ex.Code == "TIMEOUT"
                ? "导入超时：后端处理时间过长（OCR/嵌入耗时任务），已自动取消本次请求，可稍后重试或到「日志」页查看后端进度"
                : $"API 错误：{ex.Message}";
            CurrentProcessingItem = "导入请求发生异常";
            DebugLog.Error($"导入 API 错误: code={ex.Code} message={ex.Message} detail={ex.Detail} 耗时{sw.ElapsedMilliseconds}ms", "Import", ex);
        }
        catch (BackendConnectionException ex)
        {
            BackendUnreachable?.Invoke();
            sw.Stop();
            StatusMessage = $"后端不可达：{ex.Message}";
            CurrentProcessingItem = "后端服务未响应";
            DebugLog.Error($"导入后端不可达: {ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Import", ex);
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            CurrentProcessingItem = "发生未知错误";
            DebugLog.Error($"导入未知异常 耗时{sw.ElapsedMilliseconds}ms", "Import", ex);
        }
        finally
        {
            _elapsedTimer?.Stop();
            _elapsedTimer = null;
            IsBusy = false;
            _importCts?.Dispose();
            _importCts = null;
            DebugLog.Info($"导入流程结束，总耗时{sw.ElapsedMilliseconds}ms", "Import");
            // 完成后清除检查点
            if (_checkpoint is { } cp)
            {
                _ = cp.ClearCheckpointAsync(opId);
            }
            // 无论成败都通知联动方（可能部分文件已成功写入库）
            ImportCompleted?.Invoke();
            // 导入后端可能自动新建了集合（如导入到新分组），刷新下拉列表
            _ = LoadCollectionsAsync();
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
        SelectedCollectionItem = null;
        IsCreatingCollection = false;
        NewCollectionName = string.Empty;
        Recursive = false;
        Force = false;
        Results.Clear();
        Skipped.Clear();
        Failed.Clear();
        ProgressPercent = 0;
        StatusMessage = "就绪";
        ElapsedTimeText = string.Empty;
        CurrentProcessingItem = string.Empty;
        _pathInfoCts?.Cancel();
        _elapsedTimer?.Stop();
        _elapsedTimer = null;
    }
}