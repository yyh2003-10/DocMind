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
        _icon.DoubleClickCommand = new RelayCommand(ShowMainWindow);
        _icon.ForceCreate();
    }

    public void UpdateStatus(BackendState state)
    {
        StatusText = state switch
        {
            BackendState.Online => "DocMind - 在线",
            BackendState.Starting => "DocMind - 启动中…",
            BackendState.Stopping => "DocMind - 退出中…",
            _ => "DocMind - 离线",
        };
    }

    public void HideToTray() => _mainWindow.Hide();

    public void ShowMainWindow()
    {
        _mainWindow.Show();
        _mainWindow.WindowState = WindowState.Normal;
        _mainWindow.Activate();
        _mainWindow.Focus();
    }

    private void ExitApp() => Application.Current.Shutdown();

    private static System.Windows.Media.ImageSource? LoadIconImage()
    {
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

    public void Dispose() => _icon.Dispose();
}
