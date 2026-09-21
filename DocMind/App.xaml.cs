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
        public IServiceProvider ServiceProvider => _serviceProvider;
        private TrayService? _trayService;
        private static Mutex? _mutex;
        private bool _isPrimaryInstance;
        // 单实例激活：命名事件。第二个实例 Set() 后，第一实例的回调把窗口从托盘/隐藏状态恢复到前台。
        // 比 MainWindowHandle 路径可靠——窗口 Hide() 到托盘后句柄为 0，旧逻辑唤不起来。
        private static EventWaitHandle? _showWindowEvent;
        private static RegisteredWaitHandle? _showWindowRegistration;
        private static string? _instanceStamp;

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

        var settings = configuration.Get<AppSettings>() ?? new AppSettings();

        // 单例此后全程持明文（运行态），落盘出口（AppSettings.Save）统一加密。
        // 解密失败（换 Windows 用户/文件损坏）静默变空曾是 API Key 被意外抹掉的根因之一，
        // 这里显式置标志供设置页警告，并记日志。
        var rawKey = settings.LlmApiKey;
        if (SecretProtector.IsProtected(rawKey))
        {
            var plainKey = SecretProtector.Unprotect(rawKey);
            if (string.IsNullOrEmpty(plainKey))
            {
                settings.LlmKeyDecryptFailed = true;
                DebugLog.Warn("已配置的 LLM API Key 密文无法解密（换过 Windows 用户或文件损坏），本次按未配置处理", "App");
            }
            settings.LlmApiKey = plainKey;
        }

        // GitHub Token（联网搜索 GitHub 通道，按请求携带）：同样 DPAPI 密文落盘，
        // 解密失败（换 Windows 用户/文件损坏）按未配置处理（匿名额度），不阻断搜索。
        if (SecretProtector.IsProtected(settings.GithubToken))
        {
            settings.GithubToken = SecretProtector.Unprotect(settings.GithubToken);
        }

        // AI 提供商档案：逐项解密 ApiKey（内存单例持明文，落盘 Save() 时再加密）。
        // 单个档案解密失败（换 Windows 用户/文件损坏）置 KeyDecryptFailed 标记，
        // 应用该档案时由设置页/对话页提示重输，而不是静默丢 key。
        if (settings.LlmProfiles is { Count: > 0 })
        {
            foreach (var profile in settings.LlmProfiles)
            {
                if (profile is null || string.IsNullOrEmpty(profile.ApiKey) || !SecretProtector.IsProtected(profile.ApiKey))
                {
                    continue;
                }
                var plain = SecretProtector.Unprotect(profile.ApiKey);
                if (string.IsNullOrEmpty(plain))
                {
                    profile.KeyDecryptFailed = true;
                    DebugLog.Warn($"AI 提供商档案「{profile.Name}」的 API Key 密文无法解密，应用时将提示重输", "App");
                }
                profile.ApiKey = plain;
            }
        }

        // 已知不可用的重排模型（fastembed TextCrossEncoder 支持列表外）启动即纠正，
        // 避免 DOC2MIND_RERANK_MODEL 环境变量注入后每轮对话「检索降级」。
        // 典型污染源：旧版默认值 Xenova/bge-reranker-v2-m3。
        var rerankModel = settings.RerankModel?.Trim() ?? "";
        if (rerankModel.Length == 0
            || rerankModel.Contains("Xenova", StringComparison.OrdinalIgnoreCase)
            || rerankModel.EndsWith("/bge-reranker-v2-m3", StringComparison.OrdinalIgnoreCase))
        {
            var corrected = "BAAI/bge-reranker-base";
            DebugLog.Warn(
                $"重排模型「{settings.RerankModel}」不在 fastembed 支持列表，已自动改为 {corrected}",
                "App");
            settings.RerankModel = corrected;
            try
            {
                settings.Save();
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"纠正后的重排模型写回 appsettings 失败：{ex.Message}", "App");
            }
        }

        return settings;
    }

        private static void ConfigureServices(IServiceCollection services, AppSettings settings)
        {
            services.AddSingleton(settings);

            // 通知服务
            services.AddSingleton<NotificationService>();

            // 主题服务
            services.AddSingleton<ThemeService>();

            // Phase 1: 跨会话记忆
            services.AddSingleton<UserMemoryService>();
            services.AddSingleton<SessionSearchService>();

            // Phase 2: 可靠性
            services.AddSingleton<CheckpointService>();

            // Phase 3: 成本追踪
            services.AddSingleton<CostTracker>();

            // Phase 4: 反馈循环
            services.AddSingleton<FeedbackService>();

            // ViewModels
            services.AddSingleton<MainViewModel>();
            services.AddTransient<SearchViewModel>();
            services.AddTransient<ChatViewModel>();
            services.AddTransient<ImportViewModel>();
            services.AddTransient<ConvertViewModel>();
            services.AddTransient<QualityViewModel>();
            services.AddTransient<DocumentsViewModel>();
            services.AddTransient<GraphViewModel>();
            services.AddTransient<SettingsViewModel>();
            services.AddTransient<DebugLogViewModel>();

            // GPU 加速状态（Singleton，供 App 检测 + 设置页显示）
            services.AddSingleton<GpuWarningViewModel>();

            services.AddSingleton<BackendProcessService>();
            // 插件（GPU/OCR）独立进程安装器：先停后端再安装，规避运行中后端
            // 占用 numpy/paddle DLL 导致的 WinError 5
            services.AddSingleton<IPluginInstallService, PluginInstallService>();
            services.AddSingleton<Microsoft.Extensions.Logging.ILogger<PluginInstallService>>(NullLogger<PluginInstallService>.Instance);
            // 外部资源定位（poppler / 后端 Python / wheels / 模型缓存）：
            // 手动指定 + 自动寻找可用配置（快扫/全盘深扫含 U 盘）
            services.AddSingleton<ResourceLocatorService>();
            services.AddSingleton<Microsoft.Extensions.Logging.ILogger<ResourceLocatorService>>(NullLogger<ResourceLocatorService>.Instance);
            services.AddSingleton<ResourcePathPanelViewModel>();

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

        /// <summary>
        /// 主题切换等场景的"先启新进程、后退旧进程"重启：新实例先等旧进程真正退出，
        /// 再去抢单实例 Mutex。否则新实例会撞上旧实例 OnExit 里尚未释放的 Mutex
        /// （可能长达 10s 的后端停止），被判为已有实例后自行 Shutdown，自动重启失败。
        /// </summary>
        private static void WaitForRestartSource(string[] args)
        {
            for (var i = 0; i < args.Length - 1; i++)
            {
                if (!string.Equals(args[i], "--restart-wait", StringComparison.OrdinalIgnoreCase))
                    continue;

                if (!int.TryParse(args[i + 1], out var pid) || pid <= 0)
                    return;

                try
                {
                    using var old = Process.GetProcessById(pid);
                    // 旧进程 OnExit 同步等后端停止的硬上限约 10s，这里多留余量
                    old.WaitForExit(15_000);
                }
                catch (ArgumentException)
                {
                    // 旧进程已退出
                }
                catch
                {
                    // 取不到进程句柄时继续走 Mutex 路径
                }
                return;
            }
        }

        protected override void OnStartup(StartupEventArgs e)
        {
            // 主题切换自动重启：先等旧进程退出并释放 Mutex，再进入单实例检测
            WaitForRestartSource(e.Args);

            // ===== 单实例互斥：防止多开争抢后端端口 =====
            var sid = System.Security.Principal.WindowsIdentity.GetCurrent().User?.Value ?? "anon";
            _instanceStamp = sid;
            _mutex = new Mutex(true, "DocMind_SingleInstance_" + sid, out bool createdNew);
            if (!createdNew)
            {
                // 已有实例运行中——通过命名事件通知其显示窗口，再退出当前进程。
                // 比 MainWindowHandle 路径可靠：旧实例窗口 Hide() 到托盘后句柄为 0，
                // 旧逻辑找不到窗口就静默退出，表现为"点图标没反应"。
                var eventName = "DocMind_ShowWindow_" + sid;
                if (EventWaitHandle.TryOpenExisting(eventName, out var existing))
                {
                    try { existing.Set(); } catch { /* ignore */ }
                    existing.Dispose();
                }
                else
                {
                    // 事件打开失败（旧版本实例未创建事件）——回退到窗口句柄激活
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
                }
                Current.Shutdown();
                return;
            }

            _isPrimaryInstance = true;

            // 第一实例：创建命名事件并注册回调，供后续实例唤起窗口
            _showWindowEvent = new EventWaitHandle(false, EventResetMode.AutoReset, "DocMind_ShowWindow_" + sid);
            _showWindowRegistration = ThreadPool.RegisterWaitForSingleObject(
                _showWindowEvent,
                (_, _) =>
                {
                    System.Windows.Application.Current?.Dispatcher.BeginInvoke(new Action(() =>
                    {
                        _trayService?.ShowMainWindow();
                    }));
                },
                null,
                Timeout.InfiniteTimeSpan,
                executeOnlyOnce: false);

            base.OnStartup(e);

            // ===== 全局异常捕获：所有未处理异常先落日志，事后可到「调试日志」页或日志文件排查 =====
            DispatcherUnhandledException += OnDispatcherUnhandledException;
            AppDomain.CurrentDomain.UnhandledException += OnAppDomainUnhandledException;
            TaskScheduler.UnobservedTaskException += OnUnobservedTaskException;

            DebugLog.LogStartup(typeof(App).Assembly.GetName().Version?.ToString() ?? "0.0.0.0");

            // 加载已保存的主题（覆盖 App.xaml 默认 Theme.xaml）
            var themeService = _serviceProvider.GetRequiredService<ThemeService>();
            themeService.LoadInitialTheme();

            // 提前加载后端服务令牌：ViewModel 构造即 fire-and-forget 拉 v1/config 等，
            // 若令牌到 BackendProcessService.StartAsync 才加载，首批请求会 401（SeedModelFromConfigAsync
            // 失败且无重试 → 整个会话模型种子为空）。此处幂等预读，消除启动期令牌竞态。
            Doc2kbApiService.LoadAuthToken();

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
                    // 退出流程会把 _trayService 置 null，后台状态回调仍可能进来
                    _trayService?.HideToTray();
                }
            };
            // 隐藏到托盘时弹一次系统托盘气泡，引导用户从托盘恢复
            // （Windows 11 默认把托盘图标收到溢出区，用户常找不到窗口去哪了。
            // 注意不能用窗口内 Toast——窗口此刻已被 Hide()，永远没人看得见）
            var trayHintShown = false;
            _trayService.HiddenToTray += (_, _) =>
            {
                if (trayHintShown) return;
                trayHintShown = true;
                _trayService?.ShowNotification(
                    "最小化到托盘",
                    "DocMind 已最小化到系统托盘，单击托盘图标可恢复窗口；右键菜单可显示或退出");
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
                // OnExit 先 Dispose 并置空托盘再停后端；StateChanged 会从后台线程打进来
                _trayService?.UpdateStatus(state);
                // 顶栏/底栏状态灯与真实后端状态联动（安全调度到 UI 线程，防止后台线程触发时跨线程访问 DependencyObject 抛出异常）
                if (System.Windows.Application.Current?.Dispatcher is { } dispatcher)
                {
                    if (dispatcher.CheckAccess())
                    {
                        if (mainWindow.DataContext is MainViewModel vm)
                        {
                            vm.UpdateBackendState(state);
                        }
                    }
                    else
                    {
                        dispatcher.InvokeAsync(() =>
                        {
                            if (mainWindow.DataContext is MainViewModel vm)
                            {
                                vm.UpdateBackendState(state);
                            }
                        });
                    }
                }
            };
            // 后端启动失败时弹出引导弹窗（仅 AutoStart 模式下触发一次）
            if (settings.AutoStartBackend)
            {
                var backendFailedShown = false;
                backend.StateChanged += (_, state) =>
                {
                    if (backendFailedShown) return;
                    // 插件安装等维护操作会主动停后端（Offline 是预期行为），不弹失败框
                    if (backend.IsMaintenance) return;
                    // Starting → Offline 表示启动失败
                    if (state == BackendState.Offline && backend.State == BackendState.Offline)
                    {
                        backendFailedShown = true;
                        System.Windows.Application.Current?.Dispatcher.Invoke(() =>
                        {
                            var detail = !string.IsNullOrWhiteSpace(backend.LastErrorMessage)
                                ? $"【底层错误诊断】\n{backend.LastErrorMessage}\n\n"
                                : "";
                            var venvDiag = Services.BackendProcessService.LastVenvHealthError is { } v
                                ? $"【环境诊断】\n{v}\n\n"
                                : "";
                            MessageBox.Show(
                                "后端服务启动失败，所有功能将不可用。\n\n" +
                                detail + venvDiag +
                                "排查建议：\n" +
                                "1. 重新安装 DocMind（新版安装包自带便携 Python 运行时）\n" +
                                "2. 或在设置页配置「后端命令」指向 Python.exe 路径\n\n" +
                                "详细日志请查看「调试日志」页面。",
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

            var notifications = _serviceProvider.GetRequiredService<NotificationService>();
            IDisposable? eventSubscription = null;

            backend.StateChanged += (_, state) =>
            {
                if (state == BackendState.Online && eventSubscription == null)
                {
                    eventSubscription = api.SubscribeEvents(msg =>
                    {
                        if (msg.Type == "file_ingested")
                        {
                            Dispatcher.InvokeAsync(() =>
                            {
                                var fileName = System.IO.Path.GetFileName(msg.Path ?? "");
                                if (msg.Result == "ingested")
                                {
                                    notifications.Success($"已自动摄入文件: {fileName}（{msg.Collection ?? "default"}）", "自动监控摄入");
                                }
                                else if (msg.Result == "failed")
                                {
                                    notifications.Warning($"自动摄入失败: {fileName}\n{msg.Error ?? ""}", "监控摄入失败");
                                }
                            });

                            try
                            {
                                var docsVm = _serviceProvider.GetService<DocumentsViewModel>();
                                docsVm?.InvalidateCache();
                            }
                            catch { }
                        }
                    });
                }
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
                    // 异步触发 GPU 诊断（填充设置页「GPU 加速」卡片）
                    _ = gpuWarning.DiagnoseAsync();
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

            // 非主实例（单实例检测失败后的快速退出）：不碰后端/托盘，避免误停主实例资源
            if (!_isPrimaryInstance)
            {
                try { _mutex?.Dispose(); } catch { /* ignore on exit */ }
                _mutex = null;
                base.OnExit(e);
                return;
            }

            // 先摘托盘：退出期间（尤其等后端停止的数秒内）若用户点托盘图标，
            // H.NotifyIcon.DoSingleClickAction 内部 Dispatcher.Invoke 会因
            // Dispatcher 已进入关机而抛 TaskCanceledException
            try
            {
                _trayService?.Dispose();
                _trayService = null;
            }
            catch { /* ignore on exit */ }

            try
            {
                var settings = _serviceProvider.GetRequiredService<AppSettings>();
                var backend = _serviceProvider.GetRequiredService<BackendProcessService>();
                if (settings.StopBackendOnExit)
                {
                    // StopAsync 内部 5s 优雅 + kill + 3s kill 等待，理论上限 ~8s。
                    // 这里给 10s 硬上限，超时放弃等待——保证退出流程必然走完，
                    // Mutex/事件必然释放，杜绝"叉掉后进程残留后台、Mutex 永不释放"。
                    // Task.Run 脱离 UI SynchronizationContext，避免 UI 线程 GetResult 死锁。
                    try
                    {
                        var stop = Task.Run(() => backend.StopAsync());
                        if (!stop.Wait(TimeSpan.FromSeconds(10)))
                        {
                            DebugLog.Warn("后端停止超过 10s，放弃等待直接退出", "App");
                        }
                    }
                    catch (Exception ex)
                    {
                        DebugLog.Warn($"退出时停止后端异常（忽略）: {ex.Message}", "App");
                    }
                }
                backend.Dispose();
            }
            catch { /* ignore on exit */ }
            try
            {
                if (_showWindowRegistration != null)
                {
                    _showWindowRegistration.Unregister(null);
                    _showWindowRegistration = null;
                }
                _showWindowEvent?.Dispose();
                _showWindowEvent = null;
            }
            catch { /* ignore on exit */ }
            try { _mutex?.ReleaseMutex(); } catch { /* ignore on exit */ }
            try { _mutex?.Dispose(); } catch { /* ignore on exit */ }
            _mutex = null;
            base.OnExit(e);
        }
    }

    /// <summary>Win32 P/Invoke 用于激活已有实例窗口与窗口最大化边界计算。</summary>
    internal static class NativeMethods
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern bool SetForegroundWindow(IntPtr hWnd);

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        internal const int SW_RESTORE = 9;

        internal const int WM_GETMINMAXINFO = 0x0024;

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint dwFlags);

        internal const uint MONITOR_DEFAULTTONEAREST = 0x00000002;

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        [return: System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.Bool)]
        internal static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFO lpmi);

        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        internal struct POINT
        {
            public int x;
            public int y;
        }

        /// <summary>ptMaxPosition 是相对显示器左上角的偏移；ptMaxSize 是最大化后的宽高。</summary>
        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        internal struct MINMAXINFO
        {
            public POINT ptReserved;
            public POINT ptMaxSize;
            public POINT ptMaxPosition;
            public POINT ptMinTrackSize;
            public POINT ptMaxTrackSize;
        }

        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        internal struct RECT
        {
            public int left;
            public int top;
            public int right;
            public int bottom;
        }

        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        internal struct MONITORINFO
        {
            public int cbSize;
            public RECT rcMonitor;
            public RECT rcWork;
            public int dwFlags;
        }
    }
}
