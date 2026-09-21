namespace DocMind.Controls;

using System;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;

/// <summary>
/// 基于 WebView2 + Mozilla PDF.js 的本地原著文档查证与精准段落高亮阅览控件。
/// </summary>
public partial class PdfCitationViewer : UserControl
{
    public static readonly DependencyProperty SourcePathProperty =
        DependencyProperty.Register(
            nameof(SourcePath),
            typeof(string),
            typeof(PdfCitationViewer),
            new PropertyMetadata(null, OnSourceOrCitationChanged));

    public static readonly DependencyProperty TargetPageProperty =
        DependencyProperty.Register(
            nameof(TargetPage),
            typeof(int?),
            typeof(PdfCitationViewer),
            new PropertyMetadata(null, OnSourceOrCitationChanged));

    public static readonly DependencyProperty TargetSnippetProperty =
        DependencyProperty.Register(
            nameof(TargetSnippet),
            typeof(string),
            typeof(PdfCitationViewer),
            new PropertyMetadata(null, OnSourceOrCitationChanged));

    public string? SourcePath
    {
        get => (string?)GetValue(SourcePathProperty);
        set => SetValue(SourcePathProperty, value);
    }

    public int? TargetPage
    {
        get => (int?)GetValue(TargetPageProperty);
        set => SetValue(TargetPageProperty, value);
    }

    public string? TargetSnippet
    {
        get => (string?)GetValue(TargetSnippetProperty);
        set => SetValue(TargetSnippetProperty, value);
    }

    private bool _isInitialized;
    private bool _isInitializing;
    private string? _currentLoadedPath;

    public PdfCitationViewer()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_isInitialized || _isInitializing)
        {
            return;
        }

        await InitializeWebViewAsync();
    }

    private async Task InitializeWebViewAsync()
    {
        _isInitializing = true;
        try
        {
            await PdfWebView.EnsureCoreWebView2Async();

            // 1. 定位本地 Resources/pdfjs 目录
            var baseDir = AppDomain.CurrentDomain.BaseDirectory;
            var pdfJsDir = Path.Combine(baseDir, "Resources", "pdfjs");
            if (!Directory.Exists(pdfJsDir))
            {
                var devFallback = Path.GetFullPath(Path.Combine(baseDir, "..", "..", "..", "Resources", "pdfjs"));
                if (Directory.Exists(devFallback))
                {
                    pdfJsDir = devFallback;
                }
            }

            if (!Directory.Exists(pdfJsDir))
            {
                ShowError($"PDF.js 离线引擎目录不存在: {pdfJsDir}");
                return;
            }

            // 2. 映射安全虚拟主机名以运行离线 pdf.js
            PdfWebView.CoreWebView2.SetVirtualHostNameToFolderMapping(
                "pdf.docmind.local",
                pdfJsDir,
                CoreWebView2HostResourceAccessKind.Allow);

            // 3. 拦截本地 PDF 文件的虚拟流式读取 (规避 CORS 和安全沙箱限制)
            PdfWebView.CoreWebView2.AddWebResourceRequestedFilter(
                "https://localpdf.docmind.local/*",
                CoreWebView2WebResourceContext.All);
            PdfWebView.CoreWebView2.WebResourceRequested += OnWebResourceRequested;

            // 4. 双向消息通信
            PdfWebView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;
            PdfWebView.CoreWebView2.NavigationCompleted += OnNavigationCompleted;

            // 5. 导航至 viewer.html
            PdfWebView.CoreWebView2.Navigate("https://pdf.docmind.local/viewer.html");
        }
        catch (Exception ex)
        {
            ShowError($"WebView2 初始化失败: {ex.Message}");
        }
        finally
        {
            _isInitializing = false;
        }
    }

    private void OnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        _isInitialized = true;
        LoadingOverlay.Visibility = Visibility.Collapsed;

        // 若初始化前已有绑定的属性，立即触发加载
        if (!string.IsNullOrWhiteSpace(SourcePath))
        {
            SyncCitationToWebView();
        }
    }

    private void OnWebResourceRequested(object? sender, CoreWebView2WebResourceRequestedEventArgs e)
    {
        try
        {
            var uri = new Uri(e.Request.Uri);
            var query = uri.Query.TrimStart('?');
            string? filePath = null;

            foreach (var part in query.Split('&', StringSplitOptions.RemoveEmptyEntries))
            {
                var kv = part.Split('=', 2);
                if (kv.Length == 2 && kv[0].Equals("path", StringComparison.OrdinalIgnoreCase))
                {
                    filePath = Uri.UnescapeDataString(kv[1]);
                    break;
                }
            }

            if (!string.IsNullOrWhiteSpace(filePath) && File.Exists(filePath))
            {
                var fs = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
                var response = PdfWebView.CoreWebView2.Environment.CreateWebResourceResponse(
                    fs,
                    200,
                    "OK",
                    "Content-Type: application/pdf\r\nAccess-Control-Allow-Origin: *\r\nAccept-Ranges: bytes");
                e.Response = response;
                return;
            }

            e.Response = PdfWebView.CoreWebView2.Environment.CreateWebResourceResponse(
                null, 404, "Not Found", "Content-Type: text/plain");
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"[PdfCitationViewer] 读取本地 PDF 资源失败: {ex.Message}");
            e.Response = PdfWebView.CoreWebView2.Environment.CreateWebResourceResponse(
                null, 500, "Internal Error", "Content-Type: text/plain");
        }
    }

    private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        try
        {
            var json = e.WebMessageAsJson;
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.TryGetProperty("event", out var evtProp))
            {
                var evt = evtProp.GetString();
                if (evt == "pdfLoaded")
                {
                    LoadingOverlay.Visibility = Visibility.Collapsed;
                    LocateFailBanner.Visibility = Visibility.Collapsed;
                }
                else if (evt == "citationLocated")
                {
                    bool success = doc.RootElement.TryGetProperty("success", out var ok)
                        && ok.ValueKind == JsonValueKind.True;
                    // 有 snippet 却未匹配到原文时明确提示，禁止静默失败
                    if (!success && !string.IsNullOrWhiteSpace(TargetSnippet))
                    {
                        var page = doc.RootElement.TryGetProperty("page", out var pg)
                            && pg.TryGetInt32(out var pgv) ? pgv : (TargetPage ?? 1);
                        LocateFailText.Text =
                            $"未能自动定位到 P{page} 的原文段落，已跳转该页；请对照引用原文核对。";
                        LocateFailBanner.Visibility = Visibility.Visible;
                    }
                    else
                    {
                        LocateFailBanner.Visibility = Visibility.Collapsed;
                    }
                }
            }
        }
        catch { }
    }

    private static void OnSourceOrCitationChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is PdfCitationViewer viewer)
        {
            viewer.SyncCitationToWebView();
        }
    }

    private void SyncCitationToWebView()
    {
        if (!_isInitialized || PdfWebView.CoreWebView2 == null)
        {
            return;
        }

        var path = SourcePath;
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
        {
            ShowError(string.IsNullOrWhiteSpace(path) ? "未指定 PDF 文件路径" : $"文件在本地磁盘不存在：{path}");
            return;
        }

        HideError();

        var page = TargetPage ?? 1;
        var snippet = TargetSnippet ?? string.Empty;

        // 若为同一文档，直接通知翻页与高亮更新，无需重新载入整个 PDF
        if (string.Equals(_currentLoadedPath, path, StringComparison.OrdinalIgnoreCase))
        {
            var jumpCmd = JsonSerializer.Serialize(new
            {
                action = "jumpTo",
                page = page,
                snippet = snippet,
            });
            PdfWebView.CoreWebView2.PostWebMessageAsJson(jumpCmd);
        }
        else
        {
            _currentLoadedPath = path;
            LoadingOverlay.Visibility = Visibility.Visible;

            var streamUrl = $"https://localpdf.docmind.local/?path={Uri.EscapeDataString(path)}";
            var loadCmd = JsonSerializer.Serialize(new
            {
                action = "loadPdf",
                url = streamUrl,
                page = page,
                snippet = snippet,
            });
            PdfWebView.CoreWebView2.PostWebMessageAsJson(loadCmd);
        }
    }

    private void ShowError(string message)
    {
        ErrorDetailText.Text = message;
        ErrorOverlay.Visibility = Visibility.Visible;
        LoadingOverlay.Visibility = Visibility.Collapsed;
        LocateFailBanner.Visibility = Visibility.Collapsed;
    }

    private void HideError()
    {
        ErrorOverlay.Visibility = Visibility.Collapsed;
    }

    private void OpenExternalButton_Click(object sender, RoutedEventArgs e)
    {
        if (!string.IsNullOrWhiteSpace(SourcePath) && File.Exists(SourcePath))
        {
            try
            {
                Process.Start(new ProcessStartInfo(SourcePath) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                MessageBox.Show($"外部打开失败: {ex.Message}", "提示", MessageBoxButton.OK, MessageBoxImage.Warning);
            }
        }
    }
}
