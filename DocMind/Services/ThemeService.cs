using System.Diagnostics;
using System.Windows;
using DocMind.Models;

namespace DocMind.Services;

/// <summary>主题模式。</summary>
public enum ThemeMode
{
    Light,
    Dark,
}

/// <summary>
/// 主题切换服务。在 Light / Dark 之间切换。
/// 由于 WPF 的 StaticResource 仅在控件创建时解析并缓存，
/// 运行时切换资源字典无法刷新已渲染控件的引用，
/// 因此切换后保存偏好并自动重启应用生效。
/// </summary>
public sealed class ThemeService
{
    private readonly AppSettings _settings;
    private readonly NotificationService? _notifications;

    /// <summary>当前主题。</summary>
    public ThemeMode CurrentTheme
    {
        get => _settings.Theme switch
        {
            "Dark" => ThemeMode.Dark,
            _ => ThemeMode.Light,
        };
    }

    /// <summary>主题变更事件。</summary>
    public event Action<ThemeMode>? ThemeChanged;

    public ThemeService(AppSettings settings, NotificationService? notifications = null)
    {
        _settings = settings;
        _notifications = notifications;
    }

    /// <summary>应用并持久化指定主题，先即时切换字典，然后自动重启使 StaticResource 完全生效。</summary>
    public void ApplyTheme(ThemeMode mode)
    {
        if (CurrentTheme == mode) return;

        // 1) 先即时切换资源字典（使新创建的控件 / 已订阅 PropertyChanged 的绑定能读到正确值）
        SwapThemeDictionary(mode);

        // 2) 持久化偏好
        _settings.Theme = mode == ThemeMode.Dark ? "Dark" : "Light";
        _settings.Save();

        // 3) 触发主窗口重绘（部分刷新已渲染控件）
        if (Application.Current.MainWindow is { } window)
        {
            var ctx = window.DataContext;
            window.DataContext = null;
            window.DataContext = ctx;
            window.InvalidateVisual();
        }

        ThemeChanged?.Invoke(mode);

        // 4) 显示通知后自动重启（解决 StaticResource 缓存不完全刷新问题）
        var themeName = mode == ThemeMode.Dark ? "深色模式" : "浅色模式";
        if (_notifications != null)
        {
            _notifications.Show(new ToastNotification
            {
                Message = $"{themeName}已切换，即将重启生效",
                Type = ToastType.Info,
                DurationMs = 1500,
            });
        }

        var timer = new System.Timers.Timer(1000) { AutoReset = false };
        timer.Elapsed += (_, _) =>
        {
            timer.Dispose();
            Application.Current.Dispatcher.Invoke(RestartApp);
        };
        timer.Start();
    }

    /// <summary>替换 Application 级资源字典中的主题。</summary>
    private void SwapThemeDictionary(ThemeMode mode)
    {
        try
        {
            ReplaceThemeDictionary(LoadThemeDictionary(mode));
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"Theme swap to {mode} failed: {ex}");
        }
    }

    /// <summary>
    /// 只替换 Theme 字典，保留 Icons 等其它 MergedDictionaries。
    /// 历史 bug：直接 merged[0] = theme 会把 Icons.xaml 顶掉，
    /// 导致 MainWindow 启动时 StaticResource IconSettings 找不到。
    /// </summary>
    private static void ReplaceThemeDictionary(ResourceDictionary themeDict)
    {
        var merged = Application.Current.Resources.MergedDictionaries;
        for (int i = 0; i < merged.Count; i++)
        {
            var src = merged[i].Source?.OriginalString ?? "";
            if (src.Contains("Theme", StringComparison.OrdinalIgnoreCase))
            {
                merged[i] = themeDict;
                return;
            }
        }
        merged.Add(themeDict);
    }

    /// <summary>启动时加载已保存的主题。</summary>
    public void LoadInitialTheme()
    {
        var mode = CurrentTheme;
        ReplaceThemeDictionary(LoadThemeDictionary(mode));
    }

    /// <summary>重启当前应用。</summary>
    /// <remarks>
    /// 必须把旧进程 PID 传给新实例（--restart-wait）：若先 Process.Start 再 Shutdown，
    /// 新进程几乎必然撞上尚未释放的单实例 Mutex，被判定"已有实例"后自行退出，
    /// 表现为切主题后程序消失、自动重启失败。新实例在抢 Mutex 前会等待本进程退出。
    /// </remarks>
    private static void RestartApp()
    {
        var exePath = Environment.ProcessPath;
        if (string.IsNullOrEmpty(exePath))
            return;

        var startInfo = new ProcessStartInfo(exePath)
        {
            UseShellExecute = true,
            Arguments = $"--restart-wait {Environment.ProcessId}",
        };

        try
        {
            Process.Start(startInfo);
        }
        catch
        {
            // 拉起失败则不要退出，避免用户丢掉当前会话
            return;
        }

        Application.Current.Shutdown();
    }

    private static ResourceDictionary LoadThemeDictionary(ThemeMode mode)
    {
        var source = mode == ThemeMode.Dark
            ? new Uri("pack://application:,,,/Styles/Theme.Dark.xaml")
            : new Uri("pack://application:,,,/Styles/Theme.xaml");
        return new ResourceDictionary { Source = source };
    }
}
