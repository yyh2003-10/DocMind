using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using DocMind.Services;
using DocMind.ViewModels;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Web.WebView2.Core;

namespace DocMind.Views;

public partial class GraphView : UserControl
{
    private bool _isWebViewInitialized;
    private bool _isInitializing;
    private int _lifecycleVersion;
    private string? _pendingJson;

    public GraphView()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
        DataContextChanged += OnDataContextChanged;
    }

    private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (e.OldValue is GraphViewModel oldVm)
        {
            oldVm.GraphDataRenderRequested -= OnGraphDataRenderRequested;
            oldVm.ThemeChangeRequested -= OnThemeChangeRequested;
            oldVm.NodeFocusRequested -= OnNodeFocusRequested;
        }

        if (e.NewValue is GraphViewModel newVm)
        {
            newVm.GraphDataRenderRequested += OnGraphDataRenderRequested;
            newVm.ThemeChangeRequested += OnThemeChangeRequested;
            newVm.NodeFocusRequested += OnNodeFocusRequested;
        }
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        var version = ++_lifecycleVersion;
        await InitializeWebViewAsync(version);
        if (version != _lifecycleVersion || !_isWebViewInitialized)
        {
            return;
        }
        if (DataContext is GraphViewModel vm)
        {
            await vm.EnsureLoadedAsync();
        }
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        // 先翻转闸门：Edge 回调可能在 WPF/COM 拆链期间触达半释放的 CoreWebView2 RCW，
        // 立即让所有回调与注入路径短路，再解绑事件，根除 ExecutionEngineException 触发条件。
        // 刻意不调用 CoreWebView2.Close()/GraphWeb.Dispose()：保证「离开再返回 Graph 页」时
        // OnLoaded 仍能复用同一 GraphWeb（EnsureCoreWebView2Async 对已初始化控件为 no-op）。
        _isWebViewInitialized = false;
        _lifecycleVersion++;
        try
        {
            var webView = GraphWeb.CoreWebView2;
            if (webView != null)
            {
                webView.WebMessageReceived -= OnWebMessageReceived;
                webView.NavigationCompleted -= OnNavigationCompleted;
            }
        }
        catch { /* 拆链期间忽略，避免在危险窗口抛二次异常 */ }
    }

    private void OnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        if (!_isWebViewInitialized)
        {
            return;
        }

        ApplyCurrentTheme();

        if (!string.IsNullOrEmpty(_pendingJson))
        {
            InjectGraphJson(_pendingJson);
            _pendingJson = null;
        }
    }

    private async Task InitializeWebViewAsync(int version)
    {
        if (_isWebViewInitialized || _isInitializing)
        {
            return;
        }

        _isInitializing = true;
        try
        {
            await GraphWeb.EnsureCoreWebView2Async();
            if (version != _lifecycleVersion)
            {
                return;
            }
            _isWebViewInitialized = true;

            try
            {
                await GraphWeb.CoreWebView2.Profile.ClearBrowsingDataAsync(CoreWebView2BrowsingDataKinds.AllDomStorage | CoreWebView2BrowsingDataKinds.CacheStorage | CoreWebView2BrowsingDataKinds.DiskCache);
            }
            catch { }

            GraphWeb.CoreWebView2.WebMessageReceived += OnWebMessageReceived;

            // 文档创建时同步预置亮色变量（HTML 默认为暗色），消除亮色用户进入图谱页时的暗色闪帧；
            // 正式 setTheme 注入后模板会自行移除该预置类
            try
            {
                var appSettings = (Application.Current as App)?.ServiceProvider.GetService<AppSettings>();
                string initialTheme = appSettings?.Theme ?? "Light";
                if (initialTheme == "Light" || initialTheme == "light")
                {
                    await GraphWeb.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                        "document.documentElement.classList.add('light-preload');");
                }
            }
            catch { }

            // 加载 HTML 模板（三级保障：项目源码路径 -> 磁盘输出目录 -> 嵌入式资源流）
            string? html = null;

            // 1. 优先从磁盘源码/输出目录加载最新模板（支持热更新与实时调试）
            string htmlPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Resources", "GraphTemplate.html");
            if (!File.Exists(htmlPath))
            {
                var sourcePath = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "..", "Resources", "GraphTemplate.html"));
                if (File.Exists(sourcePath))
                {
                    htmlPath = sourcePath;
                }
            }

            if (File.Exists(htmlPath))
            {
                try
                {
                    html = await File.ReadAllTextAsync(htmlPath);
                }
                catch { }
            }

            // 2. 磁盘文件不存在时回退到程序集嵌入资源
            if (string.IsNullOrEmpty(html))
            {
                var asm = typeof(GraphView).Assembly;
                using (var resStream = asm.GetManifestResourceStream("DocMind.Resources.GraphTemplate.html"))
                {
                    if (resStream != null)
                    {
                        using var reader = new StreamReader(resStream, System.Text.Encoding.UTF8);
                        html = await reader.ReadToEndAsync();
                    }
                }
            }

            if (!string.IsNullOrEmpty(html))
            {
                GraphWeb.NavigateToString(html);
            }
            else
            {
                DebugLog.Error("无法加载 GraphTemplate.html 模板文件", "GraphView");
            }

            GraphWeb.CoreWebView2.NavigationCompleted += OnNavigationCompleted;
        }
        catch (Exception ex)
        {
            DebugLog.Error($"WebView2 初始化失败: {ex.Message}", "GraphView", ex);
            GraphWeb.Visibility = Visibility.Collapsed;
            FallbackPanel.Visibility = Visibility.Visible;
        }
        finally
        {
            _isInitializing = false;
        }
    }

    private void ApplyCurrentTheme()
    {
        try
        {
            var appSettings = (Application.Current as App)?.ServiceProvider.GetService<AppSettings>();
            string theme = appSettings?.Theme ?? "Light";
            InjectTheme(theme);
        }
        catch { }
    }

    private void InjectTheme(string theme)
    {
        if (_isWebViewInitialized && GraphWeb.CoreWebView2 != null)
        {
            string script = $"window.setTheme && window.setTheme('{theme}');";
            GraphWeb.ExecuteScriptAsync(script);
        }
    }

    private void OnThemeChangeRequested(string theme)
    {
        InjectTheme(theme);
    }

    private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        // Unloaded 已置 false 时短路：Edge 可能仍有一帧在飞，避免触达半释放的 CoreWebView2。
        if (!_isWebViewInitialized)
        {
            return;
        }

        try
        {
            string raw = e.TryGetWebMessageAsString();
            if (string.IsNullOrWhiteSpace(raw))
            {
                return;
            }

            using var doc = JsonDocument.Parse(raw);
            var root = doc.RootElement;
            if (root.TryGetProperty("type", out var typeElem))
            {
                var type = typeElem.GetString();
                if (type == "node_click" && root.TryGetProperty("nodeId", out var idElem))
                {
                    string? nodeId = idElem.GetString();
                    if (!string.IsNullOrWhiteSpace(nodeId) && DataContext is GraphViewModel vm)
                    {
                        Dispatcher.InvokeAsync(async () => await vm.SelectNodeAsync(nodeId));
                    }
                }
                else if (type == "graph_ready")
                {
                    ApplyCurrentTheme();
                    if (DataContext is GraphViewModel vm)
                    {
                        string jsonToInject = !string.IsNullOrWhiteSpace(vm.GraphJson)
                            ? vm.GraphJson
                            : (!string.IsNullOrEmpty(_pendingJson) ? _pendingJson : "{\"nodes\":[],\"edges\":[]}");
                        InjectGraphJson(jsonToInject);
                        _pendingJson = null;
                    }
                    else if (!string.IsNullOrEmpty(_pendingJson))
                    {
                        InjectGraphJson(_pendingJson);
                        _pendingJson = null;
                    }
                    else
                    {
                        InjectGraphJson("{\"nodes\":[],\"edges\":[]}");
                    }
                }
                else if (type == "extract_requested" && DataContext is GraphViewModel vm)
                {
                    Dispatcher.InvokeAsync(async () => await vm.ExtractGraphCommand.ExecuteAsync(null));
                }
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"处理 WebView2 消息失败: {ex.Message}", "GraphView");
        }
    }

    private void OnGraphDataRenderRequested(string json)
    {
        _pendingJson = json;
        if (_isWebViewInitialized && GraphWeb.CoreWebView2 != null)
        {
            InjectGraphJson(json);
        }
    }

    private void OnNodeFocusRequested(string nodeId)
    {
        try
        {
            if (_isWebViewInitialized && GraphWeb.CoreWebView2 != null && !string.IsNullOrWhiteSpace(nodeId))
            {
                string encoded = JsonSerializer.Serialize(nodeId);
                string script = $"window.focusNode && window.focusNode({encoded});";
                GraphWeb.ExecuteScriptAsync(script);
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"执行节点聚焦失败: {ex.Message}", "GraphView");
        }
    }

    private void InjectGraphJson(string json)
    {
        try
        {
            if (GraphWeb.CoreWebView2 != null && !string.IsNullOrWhiteSpace(json))
            {
                // 唯一渲染通道：PostWebMessageAsJson（安全高效传递任意大小与结构 JSON）。
                // 模板内 chrome.webview 'message' 监听器收到后调用 window.renderGraph 渲染。
                // 修复前此处还走第二条 ExecuteScriptAsync(window.renderGraph(...)) 注入，
                // 导致每份数据 initFluidWaterSphereGraph 执行两遍（重复初始化、动画状态
                // 重置、性能浪费）——数据注入都在 graph_ready 之后，双通道纯属冗余（AUD-010）。
                GraphWeb.CoreWebView2.PostWebMessageAsJson(json);
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"执行 JS 注入失败: {ex.Message}", "GraphView");
        }
    }

    /// <summary>折叠/展开消息的「思考过程」区（按钮 DataContext 即消息实例）。</summary>
    private void ToggleThinking_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is ChatMessage msg)
        {
            msg.IsThinkingExpanded = !msg.IsThinkingExpanded;
        }
    }

    private void FlowDocViewer_Loaded(object sender, RoutedEventArgs e)
    {
        if (sender is FlowDocumentScrollViewer viewer)
        {
            var expr = System.Windows.Data.BindingOperations.GetBindingExpression(viewer, FlowDocumentScrollViewer.DocumentProperty);
            expr?.UpdateTarget();
        }
    }

    private void FlowDocViewer_Unloaded(object sender, RoutedEventArgs e)
    {
        if (sender is FlowDocumentScrollViewer viewer)
        {
            viewer.Document = null;
        }
    }
}
