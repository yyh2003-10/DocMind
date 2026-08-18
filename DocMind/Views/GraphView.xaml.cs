using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using DocMind.Services;
using DocMind.ViewModels;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Web.WebView2.Core;

namespace DocMind.Views;

public partial class GraphView : UserControl
{
    private bool _isWebViewInitialized;
    private string? _pendingJson;

    public GraphView()
    {
        InitializeComponent();
        Loaded += OnLoaded;
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
        await InitializeWebViewAsync();
        if (DataContext is GraphViewModel vm)
        {
            await vm.EnsureLoadedAsync();
        }
    }

    private async Task InitializeWebViewAsync()
    {
        if (_isWebViewInitialized)
        {
            return;
        }

        try
        {
            await GraphWeb.EnsureCoreWebView2Async();
            _isWebViewInitialized = true;

            try
            {
                await GraphWeb.CoreWebView2.Profile.ClearBrowsingDataAsync(CoreWebView2BrowsingDataKinds.AllDomStorage | CoreWebView2BrowsingDataKinds.CacheStorage | CoreWebView2BrowsingDataKinds.DiskCache);
            }
            catch { }

            GraphWeb.CoreWebView2.WebMessageReceived += OnWebMessageReceived;

            // 加载 HTML 模板（三级保障：嵌入式资源流 -> 磁盘输出目录 -> 项目源码路径）
            string? html = null;
            var asm = typeof(GraphView).Assembly;
            using (var resStream = asm.GetManifestResourceStream("DocMind.Resources.GraphTemplate.html"))
            {
                if (resStream != null)
                {
                    using var reader = new StreamReader(resStream, System.Text.Encoding.UTF8);
                    html = await reader.ReadToEndAsync();
                }
            }

            if (string.IsNullOrEmpty(html))
            {
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
                    html = await File.ReadAllTextAsync(htmlPath);
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

            GraphWeb.CoreWebView2.NavigationCompleted += (_, _) =>
            {
                ApplyCurrentTheme();

                if (!string.IsNullOrEmpty(_pendingJson))
                {
                    InjectGraphJson(_pendingJson);
                    _pendingJson = null;
                }
            };
        }
        catch (Exception ex)
        {
            DebugLog.Error($"WebView2 初始化失败: {ex.Message}", "GraphView", ex);
            GraphWeb.Visibility = Visibility.Collapsed;
            FallbackPanel.Visibility = Visibility.Visible;
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
                    if (DataContext is GraphViewModel vm && !string.IsNullOrWhiteSpace(vm.GraphJson) && vm.GraphJson != "{\"nodes\":[],\"edges\":[]}")
                    {
                        InjectGraphJson(vm.GraphJson);
                    }
                    else if (!string.IsNullOrEmpty(_pendingJson))
                    {
                        InjectGraphJson(_pendingJson);
                        _pendingJson = null;
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
                // 通道 1: 原生 PostWebMessage（安全传递完整 JSON）
                GraphWeb.CoreWebView2.PostWebMessageAsJson(json);

                // 通道 2: 直接作为 JS 表达式注入 window.renderGraph(...)
                string script = $"window.renderGraph && window.renderGraph({json});";
                GraphWeb.ExecuteScriptAsync(script);
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"执行 JS 注入失败: {ex.Message}", "GraphView");
        }
    }
}
