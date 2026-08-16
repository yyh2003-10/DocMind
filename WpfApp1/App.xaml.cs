using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Windows;
using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;

namespace DocMind
{
    /// <summary>
    /// Interaction logic for App.xaml
    /// </summary>
    public partial class App : Application
    {
        private readonly IServiceProvider _serviceProvider;
        private TrayService? _trayService;
        private static Mutex? _mutex;

        public App()
        {
            var services = new ServiceCollection();
            var settings = LoadSettings();
            ConfigureServices(services, settings);
            _serviceProvider = services.BuildServiceProvider();
        }

        private static AppSettings LoadSettings()
        {
            // 优先读用户级配置（%LOCALAPPDATA%\DocMind\appsettings.json），
            // 不存在时 fallback 读 exe 目录（兼容旧版安装）。
            var userConfigPath = AppSettings.ConfigPath;
            var exeConfigPath = System.IO.Path.Combine(AppContext.BaseDirectory, "appsettings.json");

            var configuration = new ConfigurationBuilder()
                .SetBasePath(AppContext.BaseDirectory)
                .AddJsonFile(exeConfigPath, optional: true, reloadOnChange: false)
                .AddJsonFile(userConfigPath, optional: true, reloadOnChange: false)
                .Build();

            return configuration.Get<AppSettings>() ?? new AppSettings();
        }

        private static void ConfigureServices(IServiceCollection services, AppSettings settings)
        {
            services.AddSingleton(settings);

            // 通知服务
            services.AddSingleton<NotificationService>();

            // 主题服务
            services.AddSingleton<ThemeService>();

            // ViewModels
            services.AddSingleton<MainViewModel>();
            services.AddTransient<SearchViewModel>();
            services.AddTransient<ChatViewModel>();
            services.AddTransient<ImportViewModel>();
            services.AddTransient<ConvertViewModel>();
            services.AddTransient<QualityViewModel>();
            services.AddTransient<DocumentsViewModel>();
            services.AddTransient<SettingsViewModel>();
            services.AddTransient<DebugLogViewModel>();

            // GPU 加速状态（Singleton，供 App 检测 + 设置页显示）
            services.AddSingleton<GpuWarningViewModel>();

            services.AddSingleton<BackendProcessService>();

            services.AddSingleton<Microsoft.Extensions.Logging.ILogger<Doc2kbApiService>>(NullLogger<Doc2kbApiService>.Instance);
            services.AddSingleton<Microsoft.Extensions.Logging.ILogger<BackendProcessService>>(NullLogger<BackendProcessService>.Instance);

            services.AddHttpClient<IDoc2kbApiService, Doc2kbApiService>((sp, client) =>
            {
                var appSettings = sp.GetRequiredService<AppSettings>();
                client.BaseAddress = new Uri(appSettings.BackendUrl);
                // 后端 OCR/嵌入是 CPU/GPU 密集耗时操作（扫描型 PDF 逐页 OCR + 向量化可达数分钟甚至更久），
                // 请求超时用独立的 RequestTimeoutSec（默认 1800s），避免长任务被 HttpClient.Timeout 掐断。
                // StartupTimeoutSec 仅用于后端进程启动握手。
                client.Timeout = TimeSpan.FromSeconds(Math.Max(120, appSettings.RequestTimeoutSec));
            });

            // Windows
            services.AddSingleton<MainWindow>();
        }

        protected override void OnStartup(StartupEventArgs e)
        {
            // ===== 单实例互斥：防止多开争抢后端端口 =====
            _mutex = new Mutex(true, "DocMind_SingleInstance_" + System.Security.Principal.WindowsIdentity.GetCurrent().User?.Value, out bool createdNew);
            if (!createdNew)
            {
                // 已有实例运行中——激活其窗口并退出当前进程
                var current = Process.GetCurrentProcess();
                foreach (var proc in Process.GetProcessesByName(current.ProcessName))
                {
                    if (proc.Id != current.Id && proc.MainWindowHandle != IntPtr.Zero)
                    {
                        NativeMethods.SetForegroundWindow(proc.MainWindowHandle);
                        NativeMethods.ShowWindow(proc.MainWindowHandle, NativeMethods.SW_RESTORE);
                        break;
                    }
                }
                Current.Shutdown();
                return;
            }

            base.OnStartup(e);

            // ===== 全局异常捕获：所有未处理异常先落日志，事后可到「调试日志」页或日志文件排查 =====
            DispatcherUnhandledException += OnDispatcherUnhandledException;
            AppDomain.CurrentDomain.UnhandledException += OnAppDomainUnhandledException;
            TaskScheduler.UnobservedTaskException += OnUnobservedTaskException;

            DebugLog.LogStartup(typeof(App).Assembly.GetName().Version?.ToString() ?? "0.0.0.0");

            // 加载已保存的主题（覆盖 App.xaml 默认 Theme.xaml）
            var themeService = _serviceProvider.GetRequiredService<ThemeService>();
            themeService.LoadInitialTheme();

            var mainWindow = _serviceProvider.GetRequiredService<MainWindow>();
            mainWindow.DataContext = _serviceProvider.GetRequiredService<MainViewModel>();
            mainWindow.Show();

            // 系统托盘
            _trayService = new TrayService(mainWindow);
            // 主窗口最小化 → 隐藏到托盘
            mainWindow.StateChanged += (_, _) =>
            {
                if (mainWindow.WindowState == WindowState.Minimized)
                {
                    _trayService.HideToTray();
                }
            };

            // 启动后端子进程（受 AutoStartBackend 开关控制；fire-and-forget；状态灯由事件回调）
            var settings = _serviceProvider.GetRequiredService<AppSettings>();
            DebugLog.Info(
                $"配置: BackendUrl={settings.BackendUrl} RequestTimeoutSec={settings.RequestTimeoutSec} " +
                $"AutoStartBackend={settings.AutoStartBackend} StopBackendOnExit={settings.StopBackendOnExit} " +
                $"AutoIngestPath='{settings.AutoIngestPath}'", "App");
            var backend = _serviceProvider.GetRequiredService<BackendProcessService>();
            backend.StateChanged += (_, state) =>
            {
                _trayService.UpdateStatus(state);
                // 顶栏/底栏状态灯与真实后端状态联动
                if (mainWindow.DataContext is MainViewModel vm)
                {
                    vm.UpdateBackendState(state);
                }
            };
            // 后端启动失败时弹出引导弹窗（仅 AutoStart 模式下触发一次）
            if (settings.AutoStartBackend)
            {
                var backendFailedShown = false;
                backend.StateChanged += (_, state) =>
                {
                    if (backendFailedShown) return;
                    // Starting → Offline 表示启动失败
                    if (state == BackendState.Offline && backend.State == BackendState.Offline)
                    {
                        backendFailedShown = true;
                        System.Windows.Application.Current.Dispatcher.Invoke(() =>
                        {
                            MessageBox.Show(
                                "后端服务启动失败，所有功能将不可用。\n\n" +
                                "请按以下步骤操作：\n" +
                                "1. 确认已运行 scripts\\setup.ps1 安装 Python 环境\n" +
                                "2. 或在设置页配置「后端命令」指向 Python 路径\n\n" +
                                "详细错误请查看「调试日志」页面。",
                                "DocMind — 后端启动失败",
                                MessageBoxButton.OK,
                                MessageBoxImage.Warning);
                        });
                    }
                };
            }
            if (settings.AutoStartBackend)
            {
                _ = backend.StartAsync(progress: new Progress<string>(msg =>
                {
                    if (mainWindow.DataContext is MainViewModel vm)
                    {
                        vm.StatusMessage = msg;
                    }
                }));
            }
            else
            {
                // 不自动拉起：仅触发一次状态轮询（接外部已运行的后端）
                _ = backend.RefreshStateAsync();
            }

            // 自动导入：后端就绪后执行一次（AutoIngestPath 非空时）。
            // 订阅 StateChanged 而非直接 await StartAsync，因为 StartAsync 是 fire-and-forget，
            // 且 AutoStartBackend=false 时由 RefreshStateAsync 探测到外部后端 Online 也会触发。
            var api = _serviceProvider.GetRequiredService<IDoc2kbApiService>();
            // 后端端口被占用顺延时（serve 自动 +1），API 客户端 BaseAddress 跟随实际端口
            backend.BackendUrlChanged += (_, url) =>
            {
                api.UpdateBaseAddress(url);
                DebugLog.Info($"后端地址变更，API 客户端已同步: {url}", "App");
            };
            var autoIngestDone = false;
            backend.StateChanged += async (_, state) =>
            {
                if (state != BackendState.Online || autoIngestDone)
                {
                    return;
                }
                autoIngestDone = true;
                if (string.IsNullOrWhiteSpace(settings.AutoIngestPath))
                {
                    return;
                }
                try
                {
                    var resp = await api.IngestAsync(new IngestRequest
                    {
                        Path = settings.AutoIngestPath.Trim(),
                        Collection = string.IsNullOrWhiteSpace(settings.AutoIngestCollection)
                            ? "default"
                            : settings.AutoIngestCollection.Trim(),
                        Recursive = settings.AutoIngestRecursive,
                    });
                    DebugLog.Info(
                        $"启动自动导入完成: ingested={resp.Ingested.Count} skipped={resp.Skipped} failed={resp.Failed}",
                        "App");
                    if (resp.Failed > 0)
                    {
                        DebugLog.Warn($"启动自动导入有 {resp.Failed} 个文件失败", "App");
                    }
                }
                catch (Exception ex)
                {
                    DebugLog.Error($"启动自动导入失败: {ex.Message}", "App", ex);
                }
            };

            // GPU 加速检测：后端就绪后查询 /v1/health，上报 GPU 状态到设置页。
            // 用户已选择"不再提示"则跳过弹 toast，但仍更新状态行。
            var gpuWarning = _serviceProvider.GetRequiredService<GpuWarningViewModel>();
            var notifications = _serviceProvider.GetRequiredService<NotificationService>();
            gpuWarning.Dismissed = settings.DismissGpuWarning;
            // 用户点击"不再提示"时持久化到 appsettings.json
            gpuWarning.OnDismissed = () =>
            {
                settings.DismissGpuWarning = true;
                settings.Save();
            };
            var gpuCheckedOnce = false;
            backend.StateChanged += async (_, state) =>
            {
                if (state != BackendState.Online || gpuCheckedOnce)
                {
                    return;
                }
                gpuCheckedOnce = true;
                try
                {
                    var health = await api.GetHealthAsync();
                    gpuWarning.UpdateFromHealth(health);
                    if (!health.GpuAvailable && !settings.DismissGpuWarning)
                    {
                        notifications.Warning(
                            "当前为 CPU 模式（嵌入推理较慢），可在设置页安装 GPU 加速包",
                            "GPU 加速");
                    }
                }
                catch (Exception ex)
                {
                    DebugLog.Warn($"GPU 状态检测失败: {ex.Message}", "App");
                }
            };
        }

        // ===================== 全局异常处理 =====================

        /// <summary>UI 线程未处理异常：先落日志再继续运行，详情可从「调试日志」页查看。</summary>
        private void OnDispatcherUnhandledException(object sender, System.Windows.Threading.DispatcherUnhandledExceptionEventArgs e)
        {
            DebugLog.Error(e.Exception, "UI", "Dispatcher 未处理异常");
            e.Handled = true; // 记录后继续运行，避免整程序崩溃
            DebugLog.Warn("已捕获 UI 异常并继续运行（详见本日志）", "UI");
        }

        /// <summary>AppDomain 级致命异常：进程即将退出，日志留下现场。</summary>
        private void OnAppDomainUnhandledException(object sender, UnhandledExceptionEventArgs e)
        {
            DebugLog.Error(
                e.ExceptionObject as Exception ?? new Exception($"非 Exception 对象: {e.ExceptionObject}"),
                "FATAL", "AppDomain 未处理异常（进程即将退出）");
        }

        /// <summary>未观察的异步任务异常（fire-and-forget 的坑），记录并标记已处理。</summary>
        private void OnUnobservedTaskException(object? sender, UnobservedTaskExceptionEventArgs e)
        {
            DebugLog.Error(e.Exception, "TASK", "未观察的异步任务异常（fire-and-forget）");
            e.SetObserved();
        }

        protected override void OnExit(ExitEventArgs e)
        {
            DebugLog.Info($"DocMind 退出 @ {DateTime.Now:yyyy-MM-dd HH:mm:ss.fff}", "App");
            try
            {
                var settings = _serviceProvider.GetRequiredService<AppSettings>();
                var backend = _serviceProvider.GetRequiredService<BackendProcessService>();
                if (settings.StopBackendOnExit)
                {
                    // 同步等待：async void OnExit 不会阻塞退出流程（await 后进程可能已退出，
                    // 优雅停止会被中断）。StopAsync 内部有 5s 优雅 + kill 兜底，阻塞等待可接受。
                    // Task.Run 脱离 UI SynchronizationContext，避免 UI 线程 GetResult 死锁。
                    Task.Run(() => backend.StopAsync().GetAwaiter().GetResult()).GetAwaiter().GetResult();
                }
                backend.Dispose();
            }
            catch { /* ignore on exit */ }
            _trayService?.Dispose();
            _mutex?.ReleaseMutex();
            base.OnExit(e);
        }
    }

    /// <summary>Win32 P/Invoke 用于激活已有实例窗口。</summary>
    internal static class NativeMethods
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern bool SetForegroundWindow(IntPtr hWnd);

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        internal const int SW_RESTORE = 9;
    }
}
