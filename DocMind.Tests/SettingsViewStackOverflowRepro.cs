using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using DocMind;
using DocMind.Services;
using DocMind.ViewModels;
using DocMind.Views;

namespace DocMind.Tests;

/// <summary>
/// 复现：打开设置页（SettingsView 赋 DataContext / Measure）时 System.StackOverflowException。
/// 日志断点：SettingsView XAML 初始化完成 → 未打出「设置页以独立窗口打开」。
/// </summary>
[Collection("SettingsFile")]
public class SettingsViewStackOverflowRepro
{
    private static SettingsViewModel CreateVm()
    {
        AppSettings.ConfigDirOverrideForTests ??= Path.Combine(
            Path.GetTempPath(), "DocMind.Tests", Guid.NewGuid().ToString("N"));
        // 模拟真实单例：已有模型列表/默认模型/云端协议，打开设置页时 ComboBox 双绑会同时初始化
        var appSettings = new AppSettings
        {
            LlmProvider = "openai",
            LlmBaseUrl = "https://api.openai.com/v1",
            LlmModel = "gpt-4o-mini",
            LlmAvailableModels = new List<string> { "gpt-4o-mini", "gpt-4o", "deepseek-chat" },
            LlmApiKey = "sk-test",
        };
        var notifications = new NotificationService();
        var themeService = new ThemeService(appSettings, notifications);
        var fake = new FakeDoc2kbApiService
        {
            OnUpdateConfig = (_, _) => Task.FromResult(new Models.BackendConfig { Notice = null }),
            OnGetGpuDiagnosis = _ => Task.FromResult(new Models.GpuDiagnosis { RecommendedPath = "cpu" }),
        };
        var backend = new BackendProcessService(appSettings);
        var gpuWarning = new GpuWarningViewModel(
            fake, backend, new FakePluginInstallService(), notifications);
        var vm = new SettingsViewModel(appSettings, notifications, themeService, fake, gpuWarning, backend);
        // 构造完后把 UseLocal/UseCloud 切到与绑定初始化相同的形态
        vm.UseLocalChat = false;
        vm.UseCloudChat = true;
        return vm;
    }

    [Fact]
    public void UseLocalChat_UseCloudChat_MutualWriteback_DoesNotStackOverflow()
    {
        var vm = CreateVm();
        // 模拟 RadioButton GroupName + TwoWay 绑定的互斥回写
        for (var i = 0; i < 50; i++)
        {
            vm.UseLocalChat = true;
            vm.UseCloudChat = false;
            vm.UseLocalChat = false;
            vm.UseCloudChat = true;
            vm.UseCloudChat = false;
            vm.UseLocalChat = true;
        }
        Assert.True(vm.UseLocalChat);
        Assert.False(vm.UseCloudChat);
    }

    [Fact]
    public void LlmModel_SelectedModelCandidate_MutualWriteback_DoesNotStackOverflow()
    {
        var vm = CreateVm();
        for (var i = 0; i < 50; i++)
        {
            vm.LlmModel = "model-a";
            vm.SelectedModelCandidate = vm.LlmModels.FirstOrDefault(m => m.Name == "model-a");
            vm.LlmModel = "model-b";
            vm.SelectedModelCandidate = new Models.LlmModelItem("model-c");
            vm.LlmModel = "gpt-4o-mini";
        }
        Assert.False(string.IsNullOrEmpty(vm.LlmModel));
    }

    [Fact]
    public void SettingsView_AttachDataContext_AndMeasure_DoesNotStackOverflow()
    {
        Exception? bg = null;
        var thread = new Thread(() =>
        {
            try
            {
                var app = new Application();
                app.Resources["BoolToVis"] = new BooleanToVisibilityConverter();
                app.Resources["BoolToVisConverter"] = new BooleanToVisibilityConverter();
                app.Resources["B2V"] = new BooleanToVisibilityConverter();
                app.Resources["LogLevelToBrush"] = new Helpers.LogLevelToBrushConverter();
                app.Resources["BoolInvertConverter"] = new Helpers.BoolInvertConverter();
                app.Resources["NullToVis"] = new Helpers.NullToVisibilityConverter();
                app.Resources["StringToVis"] = new Helpers.NullToVisibilityConverter();
                app.Resources["InvertBoolVis"] = new Helpers.BoolToVisibilityInvertConverter();
                app.Resources["ZeroToVis"] = new Helpers.ZeroToVisibilityConverter();
                app.Resources["SourceHighlight"] = new Helpers.SourceHighlightConverter();
                app.Resources.MergedDictionaries.Add(new ResourceDictionary
                {
                    Source = new Uri("pack://application:,,,/DocMind;component/Styles/Icons.xaml"),
                });
                app.Resources.MergedDictionaries.Add(new ResourceDictionary
                {
                    Source = new Uri("pack://application:,,,/DocMind;component/Styles/Theme.xaml"),
                });

                var vm = CreateVm();
                var view = new SettingsView();
                // 与 MainViewModel.ShowSettings 同序：构造 View → 赋 DataContext → 装入 Window → Show
                view.DataContext = vm;
                var win = new Window
                {
                    Content = view,
                    Width = 1080,
                    Height = 760,
                    WindowStartupLocation = WindowStartupLocation.CenterScreen,
                };
                win.Show();
                win.UpdateLayout();
                // 泵一拍 dispatcher，让布局/绑定/校验事件跑完
                win.Dispatcher.Invoke(DispatcherPriority.Loaded, () => { });
                win.Close();
            }
            catch (Exception ex)
            {
                bg = ex;
            }
        })
        {
            IsBackground = true,
        };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        if (!thread.Join(TimeSpan.FromSeconds(30)))
        {
            throw new TimeoutException("SettingsView repro thread hung (possible deadlock).");
        }
        if (bg is not null)
        {
            throw new InvalidOperationException($"Repro threw: {bg}");
        }
    }
}
