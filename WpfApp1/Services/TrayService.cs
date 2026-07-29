using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows;
using H.NotifyIcon;

namespace DocMind.Services;

/// <summary>
/// 系统托盘服务：最小化到托盘、双击恢复、右键菜单（显示/退出）。
/// </summary>
public sealed class TrayService : IDisposable
{
    private readonly TaskbarIcon _icon;
    private readonly Window _mainWindow;

    /// <summary>托盘状态灯文案：在线 / 离线 / 启动中。</summary>
    public string StatusText
    {
        get => _statusText;
        private set => SetField(ref _statusText, value);
    }
    private string _statusText = "DocMind - 离线";

    /// <summary>状态变化通知。</summary>
    public event EventHandler<string>? StatusChanged;

    public TrayService(Window mainWindow)
    {
        _mainWindow = mainWindow;
        _icon = new TaskbarIcon
        {
            ToolTipText = StatusText,
            // 嵌入资源图标（无 .ico 时退到系统信息图标，避免硬崩）
            IconSource = TryLoadIcon(),
            ContextMenuStripItems = new[]
            {
                new ToolStripMenuItemInfo
                {
                    Text = "显示主窗口",
                    Click = (s, e) => ShowMainWindow(),
                },
                new ToolStripMenuItemInfo
                {
                    Text = "退出",
                    Click = (s, e) => ExitApp(),
                },
            },
        };
        _icon.DoubleClickCommand = new RelayCommand(ShowMainWindow);
        _icon.ForceCreate();
    }

    /// <summary>更新状态灯文案（后端在线/离线时调）。</summary>
    public void UpdateStatus(BackendState state)
    {
        StatusText = state switch
        {
            BackendState.Online => "DocMind - 在线",
            BackendState.Starting => "DocMind - 启动中…",
            BackendState.Stopping => "DocMind - 退出中…",
            _ => "DocMind - 离线",
        };
        _icon.ToolTipText = StatusText;
        StatusChanged?.Invoke(this, StatusText);
    }

    /// <summary>隐藏主窗口到托盘（不显示在任务栏）。</summary>
    public void HideToTray()
    {
        _mainWindow.Hide();
    }

    /// <summary>从托盘恢复主窗口。</summary>
    public void ShowMainWindow()
    {
        _mainWindow.Show();
        _mainWindow.WindowState = WindowState.Normal;
        _mainWindow.Activate();
        _mainWindow.Focus();
    }

    /// <summary>退出整个应用（触发 Application.Shutdown）。</summary>
    private void ExitApp()
    {
        Application.Current.Shutdown();
    }

    /// <summary>尝试从嵌入资源加载 DocMind.ico；失败回 null（H.NotifyIcon 会用默认）。</summary>
    private static System.Windows.Media.ImageSource? TryLoadIcon()
    {
        try
        {
            var uri = new Uri("pack://application:,,,/Assets/DocMind.ico", UriKind.Absolute);
            return new System.Windows.Media.Imaging.BitmapImage(uri);
        }
        catch
        {
            return null;
        }
    }

    public void Dispose()
    {
        _icon.Dispose();
    }

    // --- 简易 INPC 与 RelayCommand（避免新增依赖） ---
    private bool SetField<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }
        field = value;
        StatusChanged?.Invoke(this, StatusText);
        return true;
    }

    private sealed class RelayCommand : System.Windows.Input.ICommand
    {
        private readonly Action _action;
        public RelayCommand(Action action) => _action = action;
        public bool CanExecute(object? parameter) => true;
        public void Execute(object? parameter) => _action();
        public event EventHandler? CanExecuteChanged { add { } remove { } }
    }
}
