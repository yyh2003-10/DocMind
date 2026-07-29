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
            var dict = LoadThemeDictionary(mode);
            var merged = Application.Current.Resources.MergedDictionaries;
            if (merged.Count > 0)
                merged[0] = dict;
            else
                merged.Add(dict);
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"Theme swap to {mode} failed: {ex}");
        }
    }

    /// <summary>启动时加载已保存的主题。</summary>
    public void LoadInitialTheme()
    {
        var mode = CurrentTheme;
        var dict = LoadThemeDictionary(mode);
        var merged = Application.Current.Resources.MergedDictionaries;
        if (merged.Count > 0)
            merged[0] = dict;
        else
            merged.Add(dict);
    }

    /// <summary>重启当前应用。</summary>
    private static void RestartApp()
    {
        var exePath = Environment.ProcessPath;
        if (string.IsNullOrEmpty(exePath))
            return;

        var startInfo = new ProcessStartInfo(exePath)
        {
            UseShellExecute = true,
        };
        Process.Start(startInfo);
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
