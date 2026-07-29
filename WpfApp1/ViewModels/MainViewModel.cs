using System.Collections.ObjectModel;
using DocMind.Services;

namespace DocMind.ViewModels;

public class MainViewModel : ViewModelBase
{
    public IDoc2kbApiService ApiService { get; }
    private ViewModelBase? _currentPage;
    private NavigationItem? _selectedNavigationItem;
    private string _statusMessage = "就绪";

    /// <summary>顶栏状态消息（后端启动 / 健康检查等）。</summary>
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

        NavigationItems.Add(new NavigationItem { Title = "搜索", Icon = "🔍", ViewModelType = typeof(SearchViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "导入", Icon = "📥", ViewModelType = typeof(ImportViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "转换", Icon = "🔄", ViewModelType = typeof(ConvertViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "质量看板", Icon = "📊", ViewModelType = typeof(QualityViewModel) });
        NavigationItems.Add(new NavigationItem { Title = "设置", Icon = "⚙️", ViewModelType = typeof(SettingsViewModel) });

        SelectedNavigationItem = NavigationItems[0];
    }

    public ViewModelBase? CurrentPage
    {
        get => _currentPage;
        private set => SetProperty(ref _currentPage, value);
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
}
