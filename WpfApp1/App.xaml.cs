using System;
using System.IO;
using System.Threading;
using System.Windows;
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

        public App()
        {
            var services = new ServiceCollection();
            var settings = LoadSettings();
            ConfigureServices(services, settings);
            _serviceProvider = services.BuildServiceProvider();
        }

        private static AppSettings LoadSettings()
        {
            var configuration = new ConfigurationBuilder()
                .SetBasePath(AppContext.BaseDirectory)
                .AddJsonFile("appsettings.json", optional: true, reloadOnChange: false)
                .Build();

            return configuration.Get<AppSettings>() ?? new AppSettings();
        }

        private static void ConfigureServices(IServiceCollection services, AppSettings settings)
        {
            services.AddSingleton(settings);

            // ViewModels
            services.AddSingleton<MainViewModel>();
            services.AddTransient<SearchViewModel>();
            services.AddTransient<ImportViewModel>();
            services.AddTransient<ConvertViewModel>();
            services.AddTransient<QualityViewModel>();
            services.AddTransient<SettingsViewModel>();

            services.AddSingleton<BackendProcessService>();

            services.AddSingleton<Microsoft.Extensions.Logging.ILogger<Doc2kbApiService>>(NullLogger<Doc2kbApiService>.Instance);
            services.AddSingleton<Microsoft.Extensions.Logging.ILogger<BackendProcessService>>(NullLogger<BackendProcessService>.Instance);

            services.AddHttpClient<IDoc2kbApiService, Doc2kbApiService>((sp, client) =>
            {
                var appSettings = sp.GetRequiredService<AppSettings>();
                client.BaseAddress = new Uri(appSettings.BackendUrl);
                client.Timeout = TimeSpan.FromSeconds(appSettings.StartupTimeoutSec);
            });

            // Windows
            services.AddSingleton<MainWindow>();
        }

        protected override async void OnStartup(StartupEventArgs e)
        {
            base.OnStartup(e);

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

            // 启动后端子进程（fire-and-forget；状态灯由事件回调）
            var backend = _serviceProvider.GetRequiredService<BackendProcessService>();
            backend.StateChanged += (_, state) => _trayService.UpdateStatus(state);
            _ = backend.StartAsync(progress: new Progress<string>(msg =>
                mainWindow.DataContext is MainViewModel vm
                    ? vm.StatusMessage = msg
                    : null));
        }

        protected override async void OnExit(ExitEventArgs e)
        {
            try
            {
                var backend = _serviceProvider.GetRequiredService<BackendProcessService>();
                await backend.StopAsync();
                backend.Dispose();
            }
            catch { /* ignore on exit */ }
            _trayService?.Dispose();
            base.OnExit(e);
        }
    }

}

