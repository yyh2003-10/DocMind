using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Windows;
using CommunityToolkit.Mvvm.Input;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class MainViewModel : ViewModelBase
{
    public IDoc2kbApiService ApiService { get; }
    private ViewModelBase? _currentPage;
    private NavigationItem? _selectedNavigationItem;
    private string _statusMessage = "就绪";
    private bool _isSidebarCollapsed;

    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    public ObservableCollection<NavigationItem> NavigationItems { get; } = new();

    private readonly SearchViewModel _searchViewModel;
    private readonly ImportViewModel _importViewModel;
    private readonly ConvertViewModel _convertViewModel;
    private readonly QualityViewModel _qualityViewModel;
    private readonly SettingsViewModel _settingsViewModel;

    public MainViewModel(
        IDoc2kbApiService apiService,
        SearchViewModel searchViewModel,
        ImportViewModel importViewModel,
        ConvertViewModel convertViewModel,
        QualityViewModel qualityViewModel,
        SettingsViewModel settingsViewModel)
    {
        ApiService = apiService;
        _searchViewModel = searchViewModel;
        _importViewModel = importViewModel;
        _convertViewModel = convertViewModel;
        _qualityViewModel = qualityViewModel;
        _settingsViewModel = settingsViewModel;

        Title = "DocMind";

        NavigationItems.Add(new NavigationItem { Title = "搜索", Icon = "🔍", IconPath = "Assets/nav-search.png", ViewModelType = typeof(SearchViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "导入", Icon = "📥", IconPath = "Assets/nav-import.png", ViewModelType = typeof(ImportViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "转换", Icon = "🔄", IconPath = "Assets/nav-convert.png", ViewModelType = typeof(ConvertViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "质量看板", Icon = "📊", IconPath = "Assets/nav-quality.png", ViewModelType = typeof(QualityViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "设置", Icon = "⚙️", IconPath = "Assets/nav-settings.png", ViewModelType = typeof(SettingsViewModel) });

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
                OnPropertyChanged(nameof(CurrentStatusText));
                OnPropertyChanged(nameof(CurrentIsBusy));
                OnPropertyChanged(nameof(IsSearchActive));
                OnPropertyChanged(nameof(IsImportActive));
                OnPropertyChanged(nameof(SelectedHitInfo));
                OnPropertyChanged(nameof(ImportSummary));

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
                var type when type == typeof(ImportViewModel) => _importViewModel,
                var type when type == typeof(ConvertViewModel) => _convertViewModel,
                var type when type == typeof(QualityViewModel) => _qualityViewModel,
                var type when type == typeof(SettingsViewModel) => _settingsViewModel,
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
            if (CurrentPage == _importViewModel) return _importViewModel.StatusMessage;
            if (CurrentPage == _convertViewModel) return _convertViewModel.StatusMessage;
            if (CurrentPage == _qualityViewModel) return _qualityViewModel.StatusMessage;
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
            if (CurrentPage == _importViewModel) return _importViewModel.IsBusy;
            if (CurrentPage == _convertViewModel) return _convertViewModel.IsBusy;
            if (CurrentPage == _qualityViewModel) return _qualityViewModel.IsBusy;
            return false;
        }
    }

    // ===================== 详情面板 =====================

    public bool IsSearchActive => CurrentPage == _searchViewModel;
    public bool IsImportActive => CurrentPage == _importViewModel;

    public string? SelectedHitInfo
    {
        get
        {
            if (CurrentPage != _searchViewModel) return null;
            var hit = _searchViewModel.SelectedHit;
            return hit != null
                ? $"文档: {hit.Chunk?.DocumentId ?? "—"}\n相似度: {hit.Score:F4}"
                : "未选中结果";
        }
    }

    public string? ImportSummary
    {
        get
        {
            if (CurrentPage != _importViewModel) return null;
            return $"已导入: {_importViewModel.Results.Count}\n跳过: {_importViewModel.Skipped.Count}\n失败: {_importViewModel.Failed.Count}";
        }
    }

    private void OnPagePropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        // 当前页面子 VM 的属性变化 → 同步到 MainWindow 的绑定
        if (e.PropertyName == "StatusMessage")
            OnPropertyChanged(nameof(CurrentStatusText));
        else if (e.PropertyName == "IsBusy")
            OnPropertyChanged(nameof(CurrentIsBusy));
        else if (sender == _searchViewModel && e.PropertyName == nameof(SearchViewModel.SelectedHit))
            OnPropertyChanged(nameof(SelectedHitInfo));
        else if (sender == _importViewModel && e.PropertyName == nameof(ImportViewModel.HasResults))
            OnPropertyChanged(nameof(ImportSummary));
    }
}
