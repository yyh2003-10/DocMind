using System.Windows;
using System.Windows.Controls;
using H.NotifyIcon;

namespace DocMind.Services;

/// <summary>
/// 系统托盘服务：最小化到托盘、双击恢复、右键菜单（显示/退出）。
/// </summary>
public sealed class TrayService : IDisposable
{
    private readonly TaskbarIcon _icon;
    private readonly Window _mainWindow;
    private string _statusText = "DocMind - 离线";
    private bool _disposed;

    /// <summary>托盘状态灯文案。</summary>
    public string StatusText
    {
        get => _statusText;
        private set
        {
            if (_statusText != value)
            {
                _statusText = value;
                _icon.ToolTipText = value;
                StatusChanged?.Invoke(this, value);
            }
        }
    }

    /// <summary>状态变化通知。</summary>
    public event EventHandler<string>? StatusChanged;

    /// <summary>窗口隐藏到托盘时触发，供 App 侧弹托盘气泡提示用户（避免 TrayService 反向依赖 NotificationService）。</summary>
    public event EventHandler? HiddenToTray;

    /// <summary>
    /// 弹系统托盘气泡通知（Windows 10/11 为 Action Center 横幅）。
    /// 窗口隐藏到托盘后，窗口内 Toast 层用户根本看不见，必须走系统级通知。
    /// </summary>
    public void ShowNotification(string title, string message)
    {
        if (_disposed) return;
        try
        {
            _icon.ShowNotification(
                title, message, H.NotifyIcon.Core.NotificationIcon.Info,
                null, false, false, true, false, TimeSpan.FromSeconds(5));
        }
        catch
        {
            // 个别 Windows 版本/专注助手模式下气泡会抛异常，忽略——托盘功能本身不受影响
        }
    }

    public TrayService(Window mainWindow)
    {
        _mainWindow = mainWindow;
        _icon = new TaskbarIcon
        {
            ToolTipText = StatusText,
            IconSource = LoadIconImage(),
        };

        // 右键菜单
        var menu = new ContextMenu();
        menu.Items.Add(new MenuItem { Header = "显示主窗口", Command = new RelayCommand(ShowMainWindow) });
        menu.Items.Add(new Separator());
        menu.Items.Add(new MenuItem { Header = "退出", Command = new RelayCommand(ExitApp) });
        _icon.ContextMenu = menu;
        // 单击恢复：用户最小化到托盘后最自然的操作是单击图标。
        // 旧版只绑了 DoubleClickCommand，单击无反应，用户以为"卡住"。
        _icon.LeftClickCommand = new RelayCommand(ShowMainWindow);
        _icon.DoubleClickCommand = new RelayCommand(ShowMainWindow);
        _icon.ForceCreate();
    }

    public void UpdateStatus(BackendState state)
    {
        if (_disposed) return;
        var text = state switch
        {
            BackendState.Online => "DocMind - 在线",
            BackendState.Starting => "DocMind - 启动中…",
            BackendState.Stopping => "DocMind - 退出中…",
            _ => "DocMind - 离线",
        };

        // StateChanged 可能来自后台线程（进程监控/健康检查/退出清理），
        // TaskbarIcon 是 UI 对象，必须先切回 UI 线程再改 ToolTipText。
        // 必须用 BeginInvoke：OnExit 里 UI 线程同步阻塞在 GetResult 时用 Invoke 会死锁。
        if (_icon.Dispatcher.CheckAccess())
        {
            ApplyStatusText(text);
        }
        else
        {
            _icon.Dispatcher.BeginInvoke(() => ApplyStatusText(text));
        }
    }

    private void ApplyStatusText(string value)
    {
        if (_statusText != value)
        {
            _statusText = value;
            _icon.ToolTipText = value;
            StatusChanged?.Invoke(this, value);
        }
    }

    public void HideToTray()
    {
        if (_disposed) return;
        _mainWindow.Hide();
        HiddenToTray?.Invoke(this, EventArgs.Empty);
    }

    public void ShowMainWindow()
    {
        if (_disposed) return;
        _mainWindow.Show();
        _mainWindow.WindowState = WindowState.Normal;
        // 从托盘恢复时窗口常被其他窗口盖住、Activate() 抢不到前台。
        // Topmost 瞬时置顶再还原，是 WPF 里可靠的"强制抢前台"手法。
        _mainWindow.Topmost = true;
        _mainWindow.Activate();
        _mainWindow.Topmost = false;
        _mainWindow.Focus();
    }

    private void ExitApp() => Application.Current.Shutdown();

    private static System.Windows.Media.ImageSource? LoadIconImage()
    {
        // Assets\DocMind.ico 在 csproj 中是 <Resource>（嵌入程序集，且被 <None Remove> 后不会拷贝到输出目录），
        // 必须走 pack URI 从程序集内加载；按磁盘路径找永远 File.Exists=false，
        // 导致托盘 IconSource=null、最小化到托盘后图标消失。磁盘路径仅作兜底。
        try
        {
            return new System.Windows.Media.Imaging.BitmapImage(
                new Uri("pack://application:,,,/Assets/DocMind.ico"));
        }
        catch { }
        var icoPath = System.IO.Path.Combine(
            AppContext.BaseDirectory, "Assets/DocMind.ico");
        if (System.IO.File.Exists(icoPath))
        {
            try
            {
                return new System.Windows.Media.Imaging.BitmapImage(
                    new Uri(icoPath, UriKind.Absolute));
            }
            catch { }
        }
        return null;
    }

    private sealed class RelayCommand : System.Windows.Input.ICommand
    {
        private readonly Action _action;
        public RelayCommand(Action a) => _action = a;
        public bool CanExecute(object? p) => true;
        public void Execute(object? p) => _action();
        public event EventHandler? CanExecuteChanged { add { } remove { } }
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        try
        {
            // 先摘掉命令与菜单：退出瞬间托盘单击回调内部会 Dispatcher.Invoke，
            // Dispatcher 已关机时会抛 TaskCanceledException
            _icon.LeftClickCommand = null;
            _icon.DoubleClickCommand = null;
            _icon.ContextMenu = null;
        }
        catch { /* ignore on exit */ }
        try { _icon.Dispose(); } catch { /* ignore on exit */ }
    }
}
