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

    // ── 批次 4：全局离线恢复横幅 ──
    /// <summary>是否显示全局离线横幅（后端离线时显示，含重新检测/重启/日志按钮）。</summary>
    public bool ShowOfflineBanner => _backendState is BackendState.Offline;

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
        OnPropertyChanged(nameof(ShowOfflineBanner));
        StartBackendCommand.NotifyCanExecuteChanged();

        // 后端恢复在线时，自动刷新搜索集合、质量看板与对话页的集合/历史会话
        if (state == BackendState.Online)
        {
            _ = _searchViewModel.LoadCollectionsAsync();
            // 导入页集合下拉同理：构造时后端未就绪加载失败且无重试，
            // 恢复在线后必须补拉，否则目标分组一直只剩种子项 default。
            _ = _importViewModel.LoadCollectionsAsync();
            _ = _chatViewModel.LoadCollectionsCommand.ExecuteAsync(null);
            // 构造时会话列表可能因后端未就绪加载失败（fire-and-forget 无重试），
            // 必须在后端恢复在线后补一次刷新，否则历史会话一直空白。
            _ = _chatViewModel.RefreshSessionsAsync();
            _ = _chatViewModel.RefreshLibraryStatusAsync();
            // 同理补拉模型种子：构造时 v1/config 可能因令牌竞态 401 失败且无重试，
            // 否则整个会话 _configuredModel 为空，「默认 · xx」退回占位符、默认提供商分组缺模型。
            _ = _chatViewModel.RefreshModelSeedAsync();
            // 图谱/质量：LLM 配置可能在离线期间变更，恢复后刷新命令可用性
            _graphViewModel.NotifyLlmGateChanged();
            _qualityViewModel.NotifyLlmGateChanged();
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

    /// <summary>重新检测后端状态（离线横幅「重新检测」按钮）。</summary>
    [RelayCommand]
    private async Task RefreshBackendAsync()
    {
        StatusMessage = "正在检测后端状态…";
        try
        {
            var health = await ApiService.GetHealthAsync();
            if (health is not null)
            {
                UpdateBackendState(BackendState.Online);
                StatusMessage = "✅ 后端在线";
            }
            else
            {
                UpdateBackendState(BackendState.Offline);
                StatusMessage = "❌ 后端不可达";
            }
        }
        catch
        {
            UpdateBackendState(BackendState.Offline);
            StatusMessage = "❌ 后端不可达";
        }
    }

    /// <summary>跳转到调试日志页（离线横幅「查看日志」按钮）。</summary>
    [RelayCommand]
    private void OpenDebugLog()
    {
        SelectedNavigationItem = NavigationItems.FirstOrDefault(n => n.ViewModelType == typeof(DebugLogViewModel));
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

        // FC-07：搜索空库空态 → 一键去导入
        _searchViewModel.GoToImportRequested += () =>
        {
            SelectedNavigationItem = NavigationItems.FirstOrDefault(n => n.ViewModelType == typeof(ImportViewModel));
            StatusMessage = "请导入文档后再回到搜索页";
        };

        // 转换成功「一键导入」→ 跳转导入页并填入文件路径
        _convertViewModel.ImportRequested += OnConvertImportRequested;

        // 对话页一键直达设置页（如未配置大模型引导）
        _chatViewModel.NavigateToSettingsRequested += NavigateToSettings;
        _chatViewModel.NavigateToImportRequested += NavigateToImport;
        _chatViewModel.NavigateToDocumentsRequested += NavigateToDocuments;

        // FC-06 类前置：图谱/质量「LLM 未配置 → 设置页」
        _graphViewModel.NavigateToSettingsRequested += NavigateToSettings;
        _qualityViewModel.NavigateToSettingsRequested += NavigateToSettings;

        // FC-03/08：各业务页后端不可达 → 统一全局离线横幅
        void OnPageBackendUnreachable()
        {
            UpdateBackendState(BackendState.Offline);
            StatusMessage = "检测到后端不可达：请使用顶部横幅「重新检测」或「启动后端」";
        }
        _chatViewModel.BackendUnreachable += OnPageBackendUnreachable;
        _searchViewModel.BackendUnreachable += OnPageBackendUnreachable;
        _importViewModel.BackendUnreachable += OnPageBackendUnreachable;
        _documentsViewModel.BackendUnreachable += OnPageBackendUnreachable;
        _convertViewModel.BackendUnreachable += OnPageBackendUnreachable;
        _graphViewModel.BackendUnreachable += OnPageBackendUnreachable;
        _qualityViewModel.BackendUnreachable += OnPageBackendUnreachable;

        // 对话页来源抽屉「在搜索页查找」→ 本地来源跳搜索页检索、web 来源浏览器打开
        _chatViewModel.SourceSearchRequested += OnSourceSearchRequested;

        // 设置页服务商配置变更 → 对话页重建模型候选。由 Main 统一订阅静态事件，
        // ChatViewModel 不再自订阅（静态事件长期持有 VM 引用无法退订）
        SettingsViewModel.ProviderConfigChanged += _chatViewModel.ApplyProviderConfigChanged;

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
        NavigationItems.Add(new NavigationItem { Title = "对话", IconKey = "IconChat", Category = "工作台", ViewModelType = typeof(ChatViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "搜索", IconKey = "IconSearch", Category = "工作台", ViewModelType = typeof(SearchViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "知识图谱", IconKey = "IconGraph", Category = "工作台", ViewModelType = typeof(GraphViewModel) });

        // 分组 2：知识资产 (内容管理与生产线)
        NavigationItems.Add(new NavigationItem { Title = "文档库", IconKey = "IconFolder", Category = "知识资产", ViewModelType = typeof(DocumentsViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "导入", IconKey = "IconImport", Category = "知识资产", ViewModelType = typeof(ImportViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "转换", IconKey = "IconConvert", Category = "知识资产", ViewModelType = typeof(ConvertViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "质量看板", IconKey = "IconDashboard", Category = "知识资产", ViewModelType = typeof(QualityViewModel) });

        // 分组 3：系统与支持
        NavigationItems.Add(new NavigationItem { Title = "设置", IconKey = "IconSettings", Category = "系统与支持", ViewModelType = typeof(SettingsViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "调试日志", IconKey = "IconList", Category = "系统与支持", ViewModelType = typeof(DebugLogViewModel) });

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
                    _ = DiagnoseGpuThrottledAsync();

                // 订阅新页面
                if (_currentPage != null)
                    _currentPage.PropertyChanged += OnPagePropertyChanged;

                // 设置页用独立窗口承载，不进主页宿主（大 XAML 在宿主里会白屏）
                if (value is SettingsViewModel)
                {
                    ShowSettings();
                    // 侧栏仍高亮「设置」，但主区保持上一可视页，避免整页空白
                    return;
                }

                if (value != null)
                {
                    try
                    {
                        ShowView(ResolveView(value));
                    }
                    catch (Exception ex)
                    {
                        DebugLog.Error(ex, "Nav", $"创建页面失败: {value.GetType().Name}");
                        ShowView(CreateErrorView(value));
                    }
                }

                OnPropertyChanged(nameof(IsChatActive));
                OnPropertyChanged(nameof(DrawerWidth));
            }
        }
    }

    /// <summary>对话页面 ViewModel（供双轨侧栏抽屉直接绑定历史会话与操作）。</summary>
    // ===================== 页面视图缓存（避免每次导航重建 170KB XAML） =====================

    private readonly Dictionary<Type, System.Windows.FrameworkElement> _viewCache = new();
    private readonly System.Windows.Controls.Grid _pageHost = new()
    {
        ClipToBounds = true,
        HorizontalAlignment = System.Windows.HorizontalAlignment.Stretch,
        VerticalAlignment = System.Windows.VerticalAlignment.Stretch,
    };

    /// <summary>
    /// 页面宿主 Grid：子视图常驻树中，导航只切 Visibility。
    /// 避免 ContentControl 反复卸载/重挂（WebView/大 XAML 导致卡死、logical child 异常）。
    /// </summary>
    public System.Windows.FrameworkElement CurrentView => _pageHost;

    private System.Windows.FrameworkElement ResolveView(ViewModelBase vm)
    {
        var type = vm.GetType();
        if (_viewCache.TryGetValue(type, out var cached))
        {
            if (!ReferenceEquals(cached.DataContext, vm))
                cached.DataContext = vm;
            return cached;
        }

        var sw = System.Diagnostics.Stopwatch.StartNew();
        System.Windows.FrameworkElement view = vm switch
        {
            SearchViewModel _ => new DocMind.Views.SearchView(),
            ChatViewModel _ => new DocMind.Views.ChatView(),
            ImportViewModel _ => new DocMind.Views.ImportView(),
            ConvertViewModel _ => new DocMind.Views.ConvertView(),
            QualityViewModel _ => new DocMind.Views.QualityView(),
            DocumentsViewModel _ => new DocMind.Views.DocumentsView(),
            GraphViewModel _ => new DocMind.Views.GraphView(),
            SettingsViewModel _ => new DocMind.Views.SettingsView(),
            DebugLogViewModel _ => new DocMind.Views.DebugLogView(),
            _ => new System.Windows.Controls.ContentControl(),
        };
        view.DataContext = vm;
        // 常驻 Grid：先隐藏，首次加入后只切 Visible
        view.Visibility = System.Windows.Visibility.Collapsed;
        if (!_pageHost.Children.Contains(view))
            _pageHost.Children.Add(view);
        _viewCache[type] = view;
        DebugLog.Info($"页面视图已创建并缓存: {type.Name} 耗时 {sw.ElapsedMilliseconds}ms", "Nav");
        return view;
    }

    private static System.Windows.FrameworkElement CreateErrorView(ViewModelBase vm)
    {
        var tb = new System.Windows.Controls.TextBlock
        {
            Text = $"页面加载失败：{vm.GetType().Name}\n请查看「调试日志」中的 Nav 记录。",
            TextWrapping = System.Windows.TextWrapping.Wrap,
            Margin = new System.Windows.Thickness(24),
            FontSize = 14,
        };
        return tb;
    }

    private void ShowView(System.Windows.FrameworkElement view)
    {
        try
        {
            // 先显示目标页，再隐藏其它页——避免瞬间出现「全白/全空」中间态
            view.Visibility = System.Windows.Visibility.Visible;
            if (view.DataContext == null && _currentPage != null)
                view.DataContext = _currentPage;

            foreach (System.Windows.UIElement child in _pageHost.Children)
            {
                if (!ReferenceEquals(child, view) && child is System.Windows.FrameworkElement fe)
                    fe.Visibility = System.Windows.Visibility.Collapsed;
            }

            // 兜底：绝不能整页全空
            var anyVisible = false;
            foreach (System.Windows.UIElement child in _pageHost.Children)
            {
                if (child is System.Windows.FrameworkElement fe && fe.Visibility == System.Windows.Visibility.Visible)
                {
                    anyVisible = true;
                    break;
                }
            }
            if (!anyVisible && _viewCache.TryGetValue(typeof(ChatViewModel), out var chatView))
            {
                chatView.Visibility = System.Windows.Visibility.Visible;
                DebugLog.Warn("ShowView 兜底：恢复 ChatView", "Nav");
            }
            DebugLog.Debug($"ShowView → {view.GetType().Name} visible={view.Visibility}", "Nav");
        }
        catch (Exception ex)
        {
            DebugLog.Error(ex, "Nav", "ShowView 失败");
            // 失败时尽量恢复对话页，避免白屏
            if (_viewCache.TryGetValue(typeof(ChatViewModel), out var chatView))
                chatView.Visibility = System.Windows.Visibility.Visible;
        }
    }

    /// <summary>空闲预热重页（设置页 XAML 大），避免首次点击卡顿。</summary>
    public void PreloadHeavyViews()
    {
        System.Windows.Application.Current?.Dispatcher.BeginInvoke(
            System.Windows.Threading.DispatcherPriority.ContextIdle,
            new Action(() =>
            {
                try
                {
                    // SettingsView is hosted in its own window; do not create a second
                    // instance here because duplicate bindings can recurse through WPF.
                    DebugLog.Debug("跳过设置页预热（设置页使用独立窗口）", "Nav");
                    DebugLog.Info("设置页预热完成", "Nav");
                }
                catch (Exception ex)
                {
                    DebugLog.Warn($"设置页预热失败: {ex.Message}", "Nav");
                }
            }));
    }

    public ChatViewModel ChatViewModel => _chatViewModel;

    /// <summary>当前页面是否为对话页。</summary>
    public bool IsChatActive => _currentPage is ChatViewModel;

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

    // ===================== 双轨抽屉折叠 (DocMind Flow 2.0) =====================

    private bool _isDrawerOpen = true;

    /// <summary>工作区二级侧栏抽屉是否展开。</summary>
    public bool IsDrawerOpen
    {
        get => _isDrawerOpen;
        set
        {
            if (SetProperty(ref _isDrawerOpen, value))
            {
                OnPropertyChanged(nameof(DrawerWidth));
                OnPropertyChanged(nameof(DrawerToggleIcon));
                OnPropertyChanged(nameof(DrawerToggleTooltip));
            }
        }
    }

    /// <summary>二级侧栏当前宽度（处于对话页且展开时为 240，否则为 0）。</summary>
    public System.Windows.GridLength DrawerWidth =>
        (IsChatActive && IsDrawerOpen) ? new System.Windows.GridLength(240) : new System.Windows.GridLength(0);

    /// <summary>抽屉切换按钮图标。</summary>
    public string DrawerToggleIcon => IsDrawerOpen ? "◧" : "◨";

    /// <summary>抽屉切换按钮提示。</summary>
    public string DrawerToggleTooltip => IsDrawerOpen ? "收起会话历史抽屉" : "展开会话历史抽屉";

    [RelayCommand]
    private void ToggleDrawer() => IsDrawerOpen = !IsDrawerOpen;

    // ===================== 侧栏折叠（兼容旧版） =====================

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

    private DateTime _lastGpuDiagnoseAt = DateTime.MinValue;

    /// <summary>进入设置页时刷新 GPU 诊断；60s 内节流，避免每次导航都打后端。</summary>
    private Task DiagnoseGpuThrottledAsync()
    {
        if ((DateTime.UtcNow - _lastGpuDiagnoseAt).TotalSeconds < 60)
            return Task.CompletedTask;
        _lastGpuDiagnoseAt = DateTime.UtcNow;
        return _gpuWarning?.DiagnoseAsync() ?? Task.CompletedTask;
    }

    /// <summary>跳到设置页（命令 + 事件共用；优先按 ViewModelType 匹配）。</summary>
    [RelayCommand]
    private void NavigateToSettings() => ShowSettings();

    private System.Windows.Window? _settingsWindow;

    /// <summary>
    /// 打开设置页。用独立窗口承载，避免大 XAML 在主页宿主里白屏/测量异常。
    /// </summary>
    public void ShowSettings()
    {
        try
        {
            if (_settingsWindow is { IsLoaded: true })
            {
                _settingsWindow.Activate();
                return;
            }

            var view = new DocMind.Views.SettingsView { DataContext = _settingsViewModel };
            _settingsWindow = new System.Windows.Window
            {
                Title = "偏好设置 — DocMind",
                Content = view,
                Width = 1080,
                Height = 760,
                MinWidth = 860,
                MinHeight = 560,
                WindowStartupLocation = System.Windows.WindowStartupLocation.CenterOwner,
                Owner = System.Windows.Application.Current?.MainWindow,
                Background = System.Windows.Media.Brushes.White,
            };
            _settingsWindow.Closed += (_, _) => _settingsWindow = null;
            _settingsWindow.Show();
            DebugLog.Info("设置页以独立窗口打开", "Nav");
        }
        catch (Exception ex)
        {
            DebugLog.Error(ex, "Nav", "打开设置窗口失败");
            System.Windows.MessageBox.Show(
                $"打开设置失败：{ex.Message}",
                "DocMind",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        }
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

    /// <summary>引用来源点击：本地来源用文件名搜索知识库跳转搜索页；web 来源在浏览器中打开。</summary>
    private void OnSourceSearchRequested(Models.SourceRef src)
    {
        if (src.IsWebSource && !string.IsNullOrWhiteSpace(src.Url))
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(src.Url) { UseShellExecute = true });
            return;
        }
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

    /// <summary>文件监控事件来自后端 SSE；失效同一份文档页缓存并在当前页可见时刷新。</summary>
    public void HandleFileWatcherEvent()
        => _documentsViewModel.NotifyFileWatcherChange(CurrentPage == _documentsViewModel);

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
        _ = _importViewModel.LoadCollectionsAsync();
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

    private void OnPagePropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        // 当前页面子 VM 的属性变化 → 同步到 MainWindow 的绑定
        if (e.PropertyName == "StatusMessage")
            OnPropertyChanged(nameof(CurrentStatusText));
        else if (e.PropertyName == "IsBusy")
            OnPropertyChanged(nameof(CurrentIsBusy));
    }
}
