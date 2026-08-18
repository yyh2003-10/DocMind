using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Windows;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class MainViewModel : ViewModelBase
{
    public IDoc2kbApiService ApiService { get; }
    public AppSettings Settings { get; }
    private readonly BackendProcessService? _backendService;
    private ViewModelBase? _currentPage;
    private NavigationItem? _selectedNavigationItem;
    private string _statusMessage = "就绪";
    private bool _isSidebarCollapsed;
    private string _backendStatusText = "连接后端…";
    private Brush _backendStatusBrush = Brushes.Gray;
    private BackendState _backendState = BackendState.Offline;

    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    // ===================== 后端状态（顶栏/底栏状态灯） =====================

    /// <summary>后端状态文案（顶栏显示）。</summary>
    public string BackendStatusText
    {
        get => _backendStatusText;
        private set => SetProperty(ref _backendStatusText, value);
    }

    /// <summary>后端状态灯颜色（绿=在线，黄=启动/退出中，红=离线）。</summary>
    public Brush BackendStatusBrush
    {
        get => _backendStatusBrush;
        private set => SetProperty(ref _backendStatusBrush, value);
    }

    /// <summary>是否可手动启动后端（仅在离线时显示启动按钮）。</summary>
    public bool CanStartBackend => _backendState is BackendState.Offline;

    /// <summary>后端地址（底栏显示）。</summary>
    public string BackendUrl => Settings.BackendUrl;

    /// <summary>由 App 的后端进程服务状态事件回调，刷新顶栏/底栏状态灯。</summary>
    public void UpdateBackendState(BackendState state)
    {
        _backendState = state;
        (BackendStatusText, BackendStatusBrush) = state switch
        {
            BackendState.Online => ("后端在线", Brushes.Green),
            BackendState.Starting => ("后端启动中…", Brushes.Goldenrod),
            BackendState.Stopping => ("后端退出中…", Brushes.Goldenrod),
            _ => ("后端离线", Brushes.Red),
        };
        OnPropertyChanged(nameof(CanStartBackend));
        StartBackendCommand.NotifyCanExecuteChanged();

        // 后端恢复在线时，自动刷新搜索集合与质量看板
        if (state == BackendState.Online)
        {
            _ = _searchViewModel.LoadCollectionsAsync();
            _ = _chatViewModel.LoadCollectionsCommand.ExecuteAsync(null);
        }
    }

    /// <summary>手动启动/重连后端服务。</summary>
    [RelayCommand(CanExecute = nameof(CanStartBackend))]
    private async Task StartBackendAsync()
    {
        if (_backendService != null)
        {
            StatusMessage = "正在启动后端服务…";
            await _backendService.StartAsync(new Progress<string>(msg => StatusMessage = msg));
        }
    }

    public ObservableCollection<NavigationItem> NavigationItems { get; } = new();
    public ICollectionView NavigationItemsView { get; }

    private readonly SearchViewModel _searchViewModel;
    private readonly ChatViewModel _chatViewModel;
    private readonly ImportViewModel _importViewModel;
    private readonly ConvertViewModel _convertViewModel;
    private readonly QualityViewModel _qualityViewModel;
    private readonly DocumentsViewModel _documentsViewModel;
    private readonly GraphViewModel _graphViewModel;
    private readonly SettingsViewModel _settingsViewModel;
    private readonly DebugLogViewModel _debugLogViewModel;
    private readonly GpuWarningViewModel? _gpuWarning;

    public MainViewModel(
        IDoc2kbApiService apiService,
        AppSettings settings,
        SearchViewModel searchViewModel,
        ChatViewModel chatViewModel,
        ImportViewModel importViewModel,
        ConvertViewModel convertViewModel,
        QualityViewModel qualityViewModel,
        DocumentsViewModel documentsViewModel,
        GraphViewModel graphViewModel,
        SettingsViewModel settingsViewModel,
        DebugLogViewModel debugLogViewModel,
        GpuWarningViewModel? gpuWarning = null,
        BackendProcessService? backendService = null)
    {
        ApiService = apiService;
        Settings = settings;
        _backendService = backendService;
        _gpuWarning = gpuWarning;
        _searchViewModel = searchViewModel;
        _chatViewModel = chatViewModel;
        _importViewModel = importViewModel;
        _convertViewModel = convertViewModel;
        _qualityViewModel = qualityViewModel;
        _documentsViewModel = documentsViewModel;
        _graphViewModel = graphViewModel;
        _settingsViewModel = settingsViewModel;
        _debugLogViewModel = debugLogViewModel;

        // 文档详情「分块定位」→ 跳转搜索页执行搜索
        _documentsViewModel.ChunkSearchRequested += OnChunkSearchRequested;

        // 搜索详情「在文档库中查看」→ 跳转文档库并定位
        _searchViewModel.OpenDocumentRequested += OnSearchOpenDocumentRequested;

        // 搜索详情「基于分块提问」→ 跳转对话页并填入问题
        _searchViewModel.AskInChatRequested += OnSearchAskInChatRequested;

        // 转换成功「一键导入」→ 跳转导入页并填入文件路径
        _convertViewModel.ImportRequested += OnConvertImportRequested;

        // 导入完成 → 文档库/图谱/质量看板缓存失效并刷新
        _importViewModel.ImportCompleted += OnImportCompleted;

        // 全局后台任务状态感知联动
        _documentsViewModel.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName is nameof(DocumentsViewModel.IsReindexing) or nameof(DocumentsViewModel.ReindexProgressPercent))
            {
                OnPropertyChanged(nameof(HasBackgroundTask));
                OnPropertyChanged(nameof(BackgroundTaskSummary));
            }
        };

        _importViewModel.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName is nameof(ImportViewModel.IsBusy))
            {
                OnPropertyChanged(nameof(HasBackgroundTask));
                OnPropertyChanged(nameof(BackgroundTaskSummary));
            }
        };

        Title = "DocMind";

        // 分组 1：核心工作台 (日常问答与探索)
        NavigationItems.Add(new NavigationItem { Title = "对话", Icon = "💬", Category = "工作台", ViewModelType = typeof(ChatViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "搜索", Icon = "🔍", IconPath = "Assets/nav-search.png", Category = "工作台", ViewModelType = typeof(SearchViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "知识图谱", Icon = "🕸️", Category = "工作台", ViewModelType = typeof(GraphViewModel) });

        // 分组 2：知识资产 (内容管理与生产线)
        NavigationItems.Add(new NavigationItem { Title = "文档库", Icon = "🗂️", Category = "知识资产", ViewModelType = typeof(DocumentsViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "导入", Icon = "📥", IconPath = "Assets/nav-import.png", Category = "知识资产", ViewModelType = typeof(ImportViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "转换", Icon = "🔄", IconPath = "Assets/nav-convert.png", Category = "知识资产", ViewModelType = typeof(ConvertViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "质量看板", Icon = "📊", IconPath = "Assets/nav-quality.png", Category = "知识资产", ViewModelType = typeof(QualityViewModel) });

        // 分组 3：系统与支持
        NavigationItems.Add(new NavigationItem { Title = "设置", Icon = "⚙️", IconPath = "Assets/nav-settings.png", Category = "系统与支持", ViewModelType = typeof(SettingsViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "调试日志", Icon = "📋", Category = "系统与支持", ViewModelType = typeof(DebugLogViewModel) });

        var cvs = System.Windows.Data.CollectionViewSource.GetDefaultView(NavigationItems);
        cvs.GroupDescriptions.Add(new System.Windows.Data.PropertyGroupDescription(nameof(NavigationItem.Category)));
        NavigationItemsView = cvs;

        SelectedNavigationItem = NavigationItems[0];
    }

    public ViewModelBase? CurrentPage
    {
        get => _currentPage;
        private set
        {
            // 取消订阅旧页面
            if (_currentPage != null)
                _currentPage.PropertyChanged -= OnPagePropertyChanged;

            if (SetProperty(ref _currentPage, value))
            {
                DebugLog.Debug($"页面导航: → {value?.Title ?? "(空)"}", "Nav");
                OnPropertyChanged(nameof(CurrentStatusText));
                OnPropertyChanged(nameof(CurrentIsBusy));

                // 导航自动加载：每次进入页面时刷新数据，确保信息实时
                if (_currentPage is ChatViewModel cv)
                    _ = cv.LoadCollectionsCommand.ExecuteAsync(null);
                if (_currentPage is DocumentsViewModel dv)
                    _ = dv.RefreshCommand.ExecuteAsync(null);
                if (_currentPage is QualityViewModel qv)
                    _ = qv.EnsureLoadedAsync();
                if (_currentPage is GraphViewModel gv)
                    _ = gv.EnsureLoadedAsync();
                if (_currentPage is SettingsViewModel)
                    _ = _gpuWarning?.DiagnoseAsync();

                // 订阅新页面
                if (_currentPage != null)
                    _currentPage.PropertyChanged += OnPagePropertyChanged;
            }
        }
    }

    public NavigationItem? SelectedNavigationItem
    {
        get => _selectedNavigationItem;
        set
        {
            if (!SetProperty(ref _selectedNavigationItem, value))
            {
                return;
            }

            CurrentPage = value?.ViewModelType switch
            {
                var type when type == typeof(SearchViewModel) => _searchViewModel,
                var type when type == typeof(ChatViewModel) => _chatViewModel,
                var type when type == typeof(ImportViewModel) => _importViewModel,
                var type when type == typeof(ConvertViewModel) => _convertViewModel,
                var type when type == typeof(QualityViewModel) => _qualityViewModel,
                var type when type == typeof(DocumentsViewModel) => _documentsViewModel,
                var type when type == typeof(GraphViewModel) => _graphViewModel,
                var type when type == typeof(SettingsViewModel) => _settingsViewModel,
                var type when type == typeof(DebugLogViewModel) => _debugLogViewModel,
                _ => _searchViewModel
            };
        }
    }

    // ===================== 侧栏折叠 =====================

    /// <summary>侧栏是否折叠（仅图标模式）。</summary>
    public bool IsSidebarCollapsed
    {
        get => _isSidebarCollapsed;
        set
        {
            if (SetProperty(ref _isSidebarCollapsed, value))
            {
                OnPropertyChanged(nameof(SidebarWidth));
                OnPropertyChanged(nameof(SidebarToggleIcon));
                OnPropertyChanged(nameof(SidebarToggleTooltip));
            }
        }
    }

    /// <summary>侧栏当前宽度（GridLength）。</summary>
    public System.Windows.GridLength SidebarWidth =>
        IsSidebarCollapsed ? new System.Windows.GridLength(48) : new System.Windows.GridLength(160);

    /// <summary>折叠按钮图标：◀ / ▶</summary>
    public string SidebarToggleIcon => IsSidebarCollapsed ? "▶" : "◀";

    public string SidebarToggleTooltip => IsSidebarCollapsed ? "展开侧栏" : "折叠侧栏";

    [RelayCommand]
    private void ToggleSidebar() => IsSidebarCollapsed = !IsSidebarCollapsed;

    // ===================== 顶栏指令 =====================

    [RelayCommand]
    private void NavigateToSettings()
    {
        var settingsItem = NavigationItems.FirstOrDefault(n => n.ViewModelType == typeof(SettingsViewModel));
        if (settingsItem != null)
            SelectedNavigationItem = settingsItem;
    }

    [RelayCommand]
    private void NavigateToSearch()
    {
        var item = NavigationItems.FirstOrDefault(n => n.ViewModelType == typeof(SearchViewModel));
        if (item != null) SelectedNavigationItem = item;
    }

    [RelayCommand]
    private void NavigateToChat()
    {
        var item = NavigationItems.FirstOrDefault(n => n.ViewModelType == typeof(ChatViewModel));
        if (item != null) SelectedNavigationItem = item;
    }

    /// <summary>文档详情分块点击 → 设置搜索词并跳转搜索页。</summary>
    private void OnChunkSearchRequested(string query)
    {
        _searchViewModel.SearchWithQuery(query);
        NavigateToSearch();
    }

    /// <summary>搜索详情「在文档库中查看」→ 填入文件名并跳转文档库页。</summary>
    private void OnSearchOpenDocumentRequested(string sourcePath)
    {
        var fileName = Path.GetFileName(sourcePath);
        _documentsViewModel.SearchQuery = fileName;
        NavigateToDocuments();
    }

    /// <summary>搜索详情「基于分块提问」→ 填入问题并跳转对话页。</summary>
    private void OnSearchAskInChatRequested(string prompt)
    {
        _chatViewModel.InputText = prompt;
        NavigateToChat();
    }

    /// <summary>格式转换「一键导入」→ 填入文件路径并跳转导入页。</summary>
    private void OnConvertImportRequested(string filePath)
    {
        _importViewModel.SelectedPath = filePath;
        NavigateToImport();
    }

    /// <summary>引用来源点击：用文件名搜索知识库，跳转搜索页。</summary>
    private void OnSourceSearchRequested(Models.SourceRef src)
    {
        var query = Path.GetFileNameWithoutExtension(src.Source);
        _searchViewModel.SearchWithQuery(query);
        NavigateToSearch();
    }

    /// <summary>窗口关闭时统一取消各页面进行中的后台任务（导入轮询、重建索引轮询等）。</summary>
    public void CancelInFlightOperations()
    {
        _importViewModel.CancelImportCommand.Execute(null);
        _documentsViewModel.CancelReindexPolling();
    }

    /// <summary>导入完成 → 文档库、知识图谱、质量看板及对话集合全量同步刷新。</summary>
    private void OnImportCompleted()
    {
        // 1. 文档库：失效缓存，若正在显示则直接刷新
        _documentsViewModel.InvalidateCache();
        if (CurrentPage == _documentsViewModel)
        {
            _documentsViewModel.RefreshCommand.Execute(null);
        }

        // 2. 知识图谱与质量看板：失效缓存，下次进入或当前页自动刷新
        _graphViewModel.InvalidateCache();
        if (CurrentPage == _graphViewModel)
        {
            _ = _graphViewModel.EnsureLoadedAsync();
        }

        _qualityViewModel.InvalidateCache();
        if (CurrentPage == _qualityViewModel)
        {
            _ = _qualityViewModel.EnsureLoadedAsync();
        }

        // 3. 对话页、搜索页与文档库页：集合列表可能有新增集合，自动拉取
        _ = _chatViewModel.LoadCollectionsCommand.ExecuteAsync(null);
        _ = _searchViewModel.LoadCollectionsAsync();
        _ = _documentsViewModel.LoadCollectionsAsync();
    }

    [RelayCommand]
    private void NavigateToDocuments()
    {
        var item = NavigationItems.FirstOrDefault(n => n.ViewModelType == typeof(DocumentsViewModel));
        if (item != null) SelectedNavigationItem = item;
    }

    [RelayCommand]
    private void NavigateToImport()
    {
        var item = NavigationItems.FirstOrDefault(n => n.ViewModelType == typeof(ImportViewModel));
        if (item != null) SelectedNavigationItem = item;
    }

    [RelayCommand]
    private void NavigateToConvert()
    {
        var item = NavigationItems.FirstOrDefault(n => n.ViewModelType == typeof(ConvertViewModel));
        if (item != null) SelectedNavigationItem = item;
    }

    [RelayCommand]
    private void NavigateToQuality()
    {
        var item = NavigationItems.FirstOrDefault(n => n.ViewModelType == typeof(QualityViewModel));
        if (item != null) SelectedNavigationItem = item;
    }

    [RelayCommand]
    private void MinimizeToTray()
    {
        Application.Current.MainWindow!.Hide();
    }

    // ===================== 统一状态栏 =====================

    /// <summary>当前页面的状态文本。</summary>
    public string CurrentStatusText
    {
        get
        {
            if (CurrentPage == _searchViewModel) return _searchViewModel.StatusMessage;
            if (CurrentPage == _chatViewModel) return _chatViewModel.StatusMessage;
            if (CurrentPage == _importViewModel) return _importViewModel.StatusMessage;
            if (CurrentPage == _convertViewModel) return _convertViewModel.StatusMessage;
            if (CurrentPage == _qualityViewModel) return _qualityViewModel.StatusMessage;
            if (CurrentPage == _documentsViewModel) return _documentsViewModel.StatusMessage;
            if (CurrentPage == _settingsViewModel) return _settingsViewModel.StatusMessage;
            return "就绪";
        }
    }

    /// <summary>当前页面是否忙碌。</summary>
    public bool CurrentIsBusy
    {
        get
        {
            if (CurrentPage == _searchViewModel) return _searchViewModel.IsBusy;
            if (CurrentPage == _chatViewModel) return _chatViewModel.IsBusy;
            if (CurrentPage == _importViewModel) return _importViewModel.IsBusy;
            if (CurrentPage == _convertViewModel) return _convertViewModel.IsBusy;
            if (CurrentPage == _qualityViewModel) return _qualityViewModel.IsBusy;
            if (CurrentPage == _documentsViewModel) return _documentsViewModel.IsBusy || _documentsViewModel.IsReindexing;
            return false;
        }
    }

    /// <summary>是否有全局后台耗时任务进行中（跨页面常驻显示）。</summary>
    public bool HasBackgroundTask => _documentsViewModel.IsReindexing || _importViewModel.IsBusy;

    /// <summary>全局后台任务简述（显示在顶栏指示胶囊）。</summary>
    public string BackgroundTaskSummary
    {
        get
        {
            if (_documentsViewModel.IsReindexing)
                return $"⚡ 重建索引中 ({_documentsViewModel.ReindexProgressPercent}%)";
            if (_importViewModel.IsBusy)
                return "📥 文件摄入中…";
            return string.Empty;
        }
    }

    /// <summary>点击顶栏后台任务胶囊 → 聚焦导航至对应任务管理页面。</summary>
    [RelayCommand]
    private void FocusBackgroundTask()
    {
        if (_documentsViewModel.IsReindexing)
        {
            NavigateToDocuments();
        }
        else if (_importViewModel.IsBusy)
        {
            NavigateToImport();
        }
    }

    // ===================== 详情面板（已移除） =====================

    /// <summary>各子 View（SearchView / DocumentsView）已内嵌详情面板，
    /// MainWindow 不再提供全局详情面板。保留此属性返回 false 以兼容外部引用。</summary>
    public bool ShowDetailPanel => false;

    /// <summary>兼容保留：已不再使用。</summary>
    public bool IsSearchActive => false;

    /// <summary>兼容保留：已不再使用。</summary>
    public bool IsChatActive => false;

    /// <summary>兼容保留：已不再使用。</summary>
    public bool IsImportActive => false;

    private void OnPagePropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        // 当前页面子 VM 的属性变化 → 同步到 MainWindow 的绑定
        if (e.PropertyName == "StatusMessage")
            OnPropertyChanged(nameof(CurrentStatusText));
        else if (e.PropertyName == "IsBusy")
            OnPropertyChanged(nameof(CurrentIsBusy));
    }
}
