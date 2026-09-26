namespace DocMind.Controls;

using System;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using DocMind.Services;
using Microsoft.Web.WebView2.Core;

/// <summary>
/// HTML 体验气泡：把 AI 输出的自包含整页 HTML 渲染为独立 WebView2 沙箱文档。
///
/// 双阶段渲染（对齐 docs/handoffs/html-bubble-handoff.md 第四节）：
/// - 收集中（<see cref="IsStreaming"/>=true）：只显示轻量进度壳 + 去标签纯文本预览，
///   绝不把半截 HTML 交给 WebView2——半截页面会闪崩、布局跳动。
/// - 完稿（<see cref="IsStreaming"/>=false）：一次性 NavigateToString 整页。
///
/// 沙箱：默认禁公网（HtmlAllowCdn=false 时收紧 CSP）、禁外部导航、禁下载。
/// 高度自适应：页面内 ResizeObserver 经 postMessage 回宿主，设控件高度。
/// 引用桥：&lt;a class="cite" data-n="1"&gt; 点击 → CiteRequested 事件 → 复用 SourceRef 链路。
/// 降级：无 WebView2 Runtime 或导航失败 → 纯 HTML 源码文本气泡。
/// 每个气泡独立一个 WebView2 实例（多消息同时可见），不要全局共享。
/// </summary>
public sealed class HtmlAnswerBubble : Border
{
    public static readonly DependencyProperty HtmlSourceProperty =
        DependencyProperty.Register(
            nameof(HtmlSource),
            typeof(string),
            typeof(HtmlAnswerBubble),
            new PropertyMetadata(null, OnRenderStateChanged));

    public static readonly DependencyProperty AllowCdnProperty =
        DependencyProperty.Register(
            nameof(AllowCdn),
            typeof(bool),
            typeof(HtmlAnswerBubble),
            new PropertyMetadata(true));

    /// <summary>是否仍在流式收集中。true = 只显示进度壳与纯文本预览；false = 完稿，一次性渲染整页。
    /// 绑定消息的 IsLoading（生成中为 true），即「终帧 onDone 才完稿」。</summary>
    public static readonly DependencyProperty IsStreamingProperty =
        DependencyProperty.Register(
            nameof(IsStreaming),
            typeof(bool),
            typeof(HtmlAnswerBubble),
            new PropertyMetadata(false, OnRenderStateChanged));

    /// <summary>HTML 源码（整页，含 &lt;!DOCTYPE&gt; 或 ```html 围栏均可，会自动剥围栏）。</summary>
    public string? HtmlSource
    {
        get => (string?)GetValue(HtmlSourceProperty);
        set => SetValue(HtmlSourceProperty, value);
    }

    /// <summary>是否允许气泡引用公网 CDN 资源（false 时收紧 CSP 仅内联资源）。</summary>
    public bool AllowCdn
    {
        get => (bool)GetValue(AllowCdnProperty);
        set => SetValue(AllowCdnProperty, value);
    }

    /// <summary>是否仍在流式收集中（true = 进度壳；false = 完稿渲染）。</summary>
    public bool IsStreaming
    {
        get => (bool)GetValue(IsStreamingProperty);
        set => SetValue(IsStreamingProperty, value);
    }

    /// <summary>用户点击气泡内引用角标（data-n）时触发，参数为角标序号。宿主复用 SourceRef 打开链路。</summary>
    public event EventHandler<int>? CiteRequested;

    private readonly Grid _root;
    private readonly Border _shell;
    private readonly TextBlock _shellTitle;
    private readonly TextBlock _preview;

    private WebView2Provider? _web;
    private TextBlock? _fallback;
    private bool _isLoaded;
    private bool _rendering;
    private bool _rendered;
    private string? _loadedHtml;

    public HtmlAnswerBubble()
    {
        SetResourceReference(CornerRadiusProperty, "CornerLg");
        SetResourceReference(BackgroundProperty, "SurfaceBrush");
        // WebView2 是空域 HWND：必须自身 ClipToBounds，避免画出布局矩盖住输入条
        ClipToBounds = true;
        MaxHeight = 240;

        _shellTitle = new TextBlock
        {
            Text = "正在生成页面…",
            TextWrapping = TextWrapping.Wrap,
        };
        _shellTitle.SetResourceReference(TextBlock.ForegroundProperty, "TextSecondaryBrush");
        _shellTitle.SetResourceReference(TextBlock.FontSizeProperty, "FontSizeCaption");

        _preview = new TextBlock { TextWrapping = TextWrapping.Wrap };
        _preview.SetResourceReference(TextBlock.ForegroundProperty, "TextTertiaryBrush");
        _preview.SetResourceReference(TextBlock.FontSizeProperty, "FontSizeSmall");

        var scroll = new ScrollViewer
        {
            MaxHeight = 260,
            Margin = new Thickness(0, 6, 0, 0),
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            Content = _preview,
        };

        var panel = new StackPanel { Margin = new Thickness(12) };
        panel.Children.Add(_shellTitle);
        panel.Children.Add(scroll);

        _shell = new Border { Child = panel };
        _root = new Grid();
        _root.Children.Add(_shell);
        Child = _root;

        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
    }

    private static void OnRenderStateChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is HtmlAnswerBubble bubble)
        {
            bubble.Refresh();
        }
    }

    /// <summary>剥掉模型可能包上的 ```html 围栏，返回纯 HTML。</summary>
    public static string StripHtmlFence(string html)
    {
        if (string.IsNullOrWhiteSpace(html))
        {
            return string.Empty;
        }
        var s = html.Trim();
        if (s.StartsWith("```", StringComparison.Ordinal))
        {
            var firstLineEnd = s.IndexOf('\n');
            if (firstLineEnd > 0 && firstLineEnd < s.Length - 3)
            {
                s = s[(firstLineEnd + 1)..];
            }
            var lastFence = s.LastIndexOf("```", StringComparison.Ordinal);
            if (lastFence >= 0)
            {
                s = s[..lastFence];
            }
        }
        return s.Trim();
    }

    /// <summary>历史恢复 / 消息探测：内容像整页 HTML 或含 ```html 围栏即判为 HTML 消息。</summary>
    public static bool LooksLikeHtml(string? content)
    {
        if (string.IsNullOrWhiteSpace(content))
        {
            return false;
        }
        var s = content.TrimStart();
        if (s.StartsWith("```html", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        if (s.StartsWith("<!DOCTYPE", StringComparison.OrdinalIgnoreCase)
            || s.StartsWith("<!doctype", StringComparison.Ordinal)
            || s.StartsWith("<html", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        return false;
    }

    private static readonly Regex ScriptStyleRegex = new(
        @"<(script|style)\b[^>]*>[\s\S]*?(?:</\1\s*>|$)",
        RegexOptions.IgnoreCase | RegexOptions.Compiled);

    private static readonly Regex TagRegex = new(@"<[^>]*>", RegexOptions.Compiled);

    private static readonly Regex WhitespaceRegex = new(@"\s+", RegexOptions.Compiled);

    /// <summary>收集中预览：剥围栏 → 去 script/style → 去标签 → 压缩空白，超长时保留尾部（最新收到的内容）。</summary>
    public static string BuildPreview(string? html, int maxChars = 1200)
    {
        if (string.IsNullOrWhiteSpace(html))
        {
            return string.Empty;
        }
        var s = StripHtmlFence(html);
        if (string.IsNullOrWhiteSpace(s))
        {
            return string.Empty;
        }
        s = ScriptStyleRegex.Replace(s, " ");
        s = TagRegex.Replace(s, " ");
        s = WhitespaceRegex.Replace(s, " ").Trim();
        if (maxChars > 0 && s.Length > maxChars)
        {
            s = "…" + s[^maxChars..];
        }
        return s;
    }


    /// <summary>
    /// 把自包含 HTML 转成近似 Markdown，与 Markdown 回答共用 FlowDocument 沉浸气泡链路。
    /// 保留标题/加粗/列表/表格线性化/链接/代码/引用；丢弃 script/style/布局标签。
    /// 引用角标 &lt;a class="cite" data-n="1"&gt;[1]&lt;/a&gt; → [1]，便于 WireSourceMarkers。
    /// </summary>
    public static string HtmlToMarkdown(string? html)
    {
        if (string.IsNullOrWhiteSpace(html))
        {
            return string.Empty;
        }
        var s = StripHtmlFence(html);
        if (string.IsNullOrWhiteSpace(s))
        {
            return string.Empty;
        }

        s = Regex.Replace(s, @"<!--[\s\S]*?-->", " ");
        s = Regex.Replace(s, @"<(script|style|noscript|svg|template)\b[^>]*>[\s\S]*?</\1>", " ", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"<head\b[^>]*>[\s\S]*?</head>", " ", RegexOptions.IgnoreCase);

        // 引用角标 → [n]（去标签前）
        s = Regex.Replace(
            s,
            @"<a\b[^>]*class\s*=\s*[""'][^""']*cite[^""']*[""'][^>]*>\s*\[?(\d+)\]?\s*</a>",
            "[$1]",
            RegexOptions.IgnoreCase);

        s = Regex.Replace(s, @"<br\s*/?>", "\n", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</(p|div|section|article|header|footer|li|tr|h[1-6]|blockquote|pre)\s*>", "\n\n", RegexOptions.IgnoreCase);

        for (var i = 6; i >= 1; i--)
        {
            s = Regex.Replace(s, $@"<h{i}\b[^>]*>", new string('#', i) + " ", RegexOptions.IgnoreCase);
            s = Regex.Replace(s, $@"</h{i}\s*>", "\n\n", RegexOptions.IgnoreCase);
        }

        s = Regex.Replace(s, @"<(strong|b)\b[^>]*>", "**", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</(strong|b)\s*>", "**", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"<(em|i)\b[^>]*>", "*", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</(em|i)\s*>", "*", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"<code\b[^>]*>", "`", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</code\s*>", "`", RegexOptions.IgnoreCase);

        s = Regex.Replace(s, @"<li\b[^>]*>", "\n- ", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</li\s*>", "\n", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</(ul|ol)\s*>", "\n", RegexOptions.IgnoreCase);

        s = Regex.Replace(s, @"<blockquote\b[^>]*>", "\n> ", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</blockquote\s*>", "\n\n", RegexOptions.IgnoreCase);

        s = Regex.Replace(s, @"<pre\b[^>]*>\s*<code\b[^>]*>", "\n```\n", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</code>\s*</pre\s*>", "\n```\n", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"<pre\b[^>]*>", "\n```\n", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</pre\s*>", "\n```\n", RegexOptions.IgnoreCase);

        s = Regex.Replace(s, @"<a\b[^>]*href\s*=\s*[""']([^""']+)[""'][^>]*>([\s\S]*?)</a>", "[$2]($1)", RegexOptions.IgnoreCase);

        s = Regex.Replace(s, @"</t[dh]\s*>", " | ", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"<tr\b[^>]*>", "\n| ", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</tr\s*>", " |", RegexOptions.IgnoreCase);
        s = Regex.Replace(s, @"</table\s*>", "\n\n", RegexOptions.IgnoreCase);

        s = TagRegex.Replace(s, " ");
        s = s.Replace("&nbsp;", " ", StringComparison.OrdinalIgnoreCase)
             .Replace("&amp;", "&", StringComparison.OrdinalIgnoreCase)
             .Replace("&lt;", "<", StringComparison.OrdinalIgnoreCase)
             .Replace("&gt;", ">", StringComparison.OrdinalIgnoreCase)
             .Replace("&quot;", "\"", StringComparison.OrdinalIgnoreCase)
             .Replace("&#39;", "'", StringComparison.OrdinalIgnoreCase);
        s = Regex.Replace(s, @"[ \t\r\f\v]+", " ");
        s = Regex.Replace(s, @" *\n *", "\n");
        s = Regex.Replace(s, @"\n{3,}", "\n\n");
        return s.Trim();
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        _isLoaded = true;
        Refresh();
        RefreshTheme();
        IsVisibleChanged += (_, _) =>
        {
            if (IsVisible)
            {
                RefreshTheme();
            }
        };
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        Cleanup();
    }

    /// <summary>渲染状态统一入口：流式中只刷进度壳，完稿才走一次性渲染。</summary>
    private void Refresh()
    {
        if (IsStreaming)
        {
            ShowShell();
            return;
        }
        _ = RenderFinalAsync();
    }

    private void ShowShell()
    {
        _shellTitle.Text = "正在生成页面…";
        _preview.Text = BuildPreview(HtmlSource);
        ShowChild(_shell);
    }

    private async Task RenderFinalAsync()
    {
        if (_rendering || !_isLoaded)
        {
            // 未加载：等 OnLoaded 再重试；正在渲染：进行中的那次用最新 HtmlSource
            return;
        }

        var html = HtmlSource;
        if (string.IsNullOrWhiteSpace(html))
        {
            return;
        }
        var page = StripHtmlFence(html);
        if (string.IsNullOrWhiteSpace(page))
        {
            ShowFallback(html);
            return;
        }
        if (_rendered && string.Equals(page, _loadedHtml, StringComparison.Ordinal))
        {
            return;
        }

        _rendering = true;
        try
        {
            var web = _web?.WebView;
            if (web is null)
            {
                web = new Microsoft.Web.WebView2.Wpf.WebView2
                {
                    DefaultBackgroundColor = System.Drawing.Color.Transparent,
                };
                // 必须先挂到可视树，EnsureCoreWebView2Async 才能拿到 HWND 完成初始化
                ShowChild(web);
                try
                {
                    await web.EnsureCoreWebView2Async();
                }
                catch (Exception ex)
                {
                    // 无 WebView2 Runtime → 降级为源码文本气泡
                    DebugLog.Warn($"HtmlAnswerBubble WebView2 初始化失败，降级为文本气泡: {ex.Message}", "Chat");
                    try { web.Dispose(); } catch { }
                    ShowFallback(page);
                    return;
                }
                ConfigureSandbox(web.CoreWebView2);
                await InjectBridgeAsync(web.CoreWebView2);
                _web = new WebView2Provider(web);
            }

            _loadedHtml = page;
            _rendered = true;
            ShowChild(web);
            try
            {
                web.CoreWebView2.NavigateToString(WrapWithCsp(page));
                RefreshTheme();
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"HtmlAnswerBubble NavigateToString 失败，降级文本: {ex.Message}", "Chat");
                ShowFallback(page);
            }
        }
        finally
        {
            _rendering = false;
        }
    }

    private void ConfigureSandbox(CoreWebView2 core)
    {
        // 沙箱：禁默认脚本对话框、禁下载、禁新窗口外链导航
        core.Settings.AreDefaultScriptDialogsEnabled = false;
        core.Settings.IsBuiltInErrorPageEnabled = false;
        core.NewWindowRequested += (_, args) =>
        {
            // 拒绝一切新窗口（外链导航在 NavigationStarting 拦）
            args.Handled = true;
        };
        core.DownloadStarting += (_, args) =>
        {
            args.Handled = true;
        };
        core.NavigationStarting += (_, args) =>
        {
            // NavigateToString 的 about:blank/data 之外的导航（外链点击）全部拦截
            var uri = args.Uri ?? string.Empty;
            if (uri.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
                || uri.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                args.Cancel = true;
            }
        };
        core.WebMessageReceived += OnWebMessageReceived;
    }

    /// <summary>高度自适应 + 引用桥脚本：页面创建前注入。</summary>
    private static async Task InjectBridgeAsync(CoreWebView2 core)
    {
        const string bridge = """
            (function(){
              function post(h){ try{ window.chrome.webview.postMessage({kind:'height',h:h}); }catch(e){} }
              function measure(){ return Math.ceil(Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)); }
              function report(){ post(measure()); }
              document.addEventListener('click', function(ev){
                var a = ev.target && ev.target.closest ? ev.target.closest('a.cite') : null;
                if(a){ ev.preventDefault(); var n = parseInt(a.getAttribute('data-n')||'0',10);
                  try{ window.chrome.webview.postMessage({kind:'cite', n:n}); }catch(e){} }
              }, true);
              if(typeof ResizeObserver === 'function'){
                var ro = new ResizeObserver(report);
                document.addEventListener('DOMContentLoaded', function(){ ro.observe(document.documentElement); report(); });
              } else {
                document.addEventListener('DOMContentLoaded', report);
                setInterval(report, 1200);
              }
            })();
            """;
        await core.AddScriptToExecuteOnDocumentCreatedAsync(bridge);
    }

    private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        try
        {
            using var doc = JsonDocument.Parse(e.WebMessageAsJson);
            var kind = doc.RootElement.GetProperty("kind").GetString();
            if (kind == "height")
            {
                var h = doc.RootElement.GetProperty("h").GetDouble();
                // 钳制：上限 320。WebView2 空域不会被 WPF 父级裁剪，
                // 高度一大会直接盖住底部输入条（Flow 2.0 悬浮胶囊）。
                var clamped = Math.Clamp(h + 8, 160, 240);
                Dispatcher.Invoke(() =>
                {
                    Height = double.IsNaN(clamped) ? 420 : clamped;
                    MaxHeight = 240;
                    ClipToBounds = true;
                });
            }
            else if (kind == "cite")
            {
                var n = doc.RootElement.GetProperty("n").GetInt32();
                Dispatcher.Invoke(() => CiteRequested?.Invoke(this, n));
            }
        }
        catch
        {
            // 非 JSON / 字段缺失：忽略
        }
    }

    /// <summary>按 AllowCdn 下发 CSP，并注入 DocMind 深浅色主题 CSS（color-scheme + 变量覆盖）。</summary>
    private string WrapWithCsp(string page)
    {
        var csp = AllowCdn
            ? "default-src 'none'; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; img-src 'self' 'unsafe-inline' https: data:; font-src https: data:; connect-src https:; frame-src 'none'; object-src 'none'; base-uri 'none'"
            : "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' 'unsafe-inline' data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'";
        var isDark = IsAppDarkTheme();
        var scheme = isDark ? "dark" : "light";
        var theme = BuildThemeCss(isDark);
        var headInject =
            $"<meta http-equiv=\"Content-Security-Policy\" content=\"{csp}\">" +
            $"<meta name=\"color-scheme\" content=\"{scheme}\">" +
            $"<style id=\"docmind-theme\">{theme}</style>" +
            $"<script>(function(){{try{{document.documentElement.classList.add('{(isDark ? "docmind-dark" : "docmind-light")}');" +
            $"document.documentElement.style.colorScheme='{scheme}';}}catch(e){{}}}})();</script>";

        var idxH = page.IndexOf("<head", StringComparison.OrdinalIgnoreCase);
        if (idxH >= 0)
        {
            var headClose = page.IndexOf('>', idxH);
            if (headClose >= 0)
            {
                var wrapped = page.Insert(headClose + 1, headInject);
                // 文末再挂一份同 id 样式，压过模型页面后置 CSS / inline 白底
                var tail = $"<style id=\"docmind-theme-tail\">{theme}</style>";
                var bodyEnd = wrapped.LastIndexOf("</body>", StringComparison.OrdinalIgnoreCase);
                if (bodyEnd >= 0)
                {
                    wrapped = wrapped.Insert(bodyEnd, tail);
                }
                else
                {
                    wrapped += tail;
                }
                return wrapped;
            }
        }
        // 无 head：包一层最小文档壳，保证主题样式生效
        return $"<!DOCTYPE html><html class=\"{(isDark ? "docmind-dark" : "docmind-light")}\"><head>{headInject}</head><body>{page}</body></html>";
    }

    /// <summary>当前应用是否深色主题（Theme.xaml / Theme.Dark.xaml 的 IsDarkTheme 资源）。</summary>
    private static bool IsAppDarkTheme()
    {
        if (System.Windows.Application.Current?.TryFindResource("IsDarkTheme") is bool flag)
        {
            return flag;
        }
        // 回退：Surface 画刷亮度
        if (System.Windows.Application.Current?.TryFindResource("SurfaceBrush") is SolidColorBrush sb)
        {
            var c = sb.Color;
            return (0.299 * c.R + 0.587 * c.G + 0.114 * c.B) < 128;
        }
        return false;
    }

    /// <summary>
    /// 注入 HTML 阅读区的主题样式：CSS 变量 + 基础标签可读性覆盖。
    /// 不改页面版式结构，只保证深/浅色下正文、标题、代码、表格、链接可读。
    /// </summary>
    private static string BuildThemeCss(bool dark)
    {
        var bg = dark ? "#1C1C1E" : "#FFFFFF";
        var surface = dark ? "#242428" : "#F7F7FA";
        var text = dark ? "#F5F5F7" : "#1D1D1F";
        var muted = dark ? "#A1A1A6" : "#6B6B70";
        var primary = dark ? "#818CF8" : "#4F46E5";
        var border = dark ? "#3A3A3E" : "#E5E5EA";
        var codeBg = dark ? "#2C2C30" : "#F2F2F5";
        var codeIn = dark ? "#3A3A40" : "#ECECF0";
        var mark = dark ? "#5B4B1A" : "#FEF3C7";

        // 深色：强制压掉模型 HTML 自带的白底/黑字（含 inline style），保证基础可读
        var darkBlock = dark
            ? $@"
html.docmind-dark, html.docmind-dark body,
html.docmind-dark body > div, html.docmind-dark body > main,
html.docmind-dark body > article, html.docmind-dark body > section,
html.docmind-dark .card, html.docmind-dark .panel, html.docmind-dark .box,
html.docmind-dark section, html.docmind-dark article {{
  background-color: {bg} !important;
  background-image: none !important;
  color: {text} !important;
}}
html.docmind-dark body * {{
  /* 先抹掉页面白底，再按角色恢复 */
  background-color: transparent !important;
  background-image: none !important;
}}
html.docmind-dark body,
html.docmind-dark p, html.docmind-dark li, html.docmind-dark dd, html.docmind-dark dt,
html.docmind-dark td, html.docmind-dark th, html.docmind-dark span, html.docmind-dark div,
html.docmind-dark h1, html.docmind-dark h2, html.docmind-dark h3,
html.docmind-dark h4, html.docmind-dark h5, html.docmind-dark h6,
html.docmind-dark strong, html.docmind-dark b, html.docmind-dark em, html.docmind-dark i {{
  color: {text} !important;
}}
html.docmind-dark a, html.docmind-dark a.cite {{ color: {primary} !important; }}
html.docmind-dark pre, html.docmind-dark code, html.docmind-dark kbd, html.docmind-dark samp {{
  background-color: {codeBg} !important;
  color: {text} !important;
  border-color: {border} !important;
}}
html.docmind-dark code {{ background-color: {codeIn} !important; }}
html.docmind-dark pre code {{ background-color: transparent !important; }}
html.docmind-dark blockquote {{
  background-color: {surface} !important;
  color: {muted} !important;
  border-left: 3px solid {primary} !important;
}}
html.docmind-dark th {{ background-color: {surface} !important; }}
html.docmind-dark tr:nth-child(even) td {{ background-color: {surface} !important; }}
html.docmind-dark .card, html.docmind-dark .panel, html.docmind-dark .box,
html.docmind-dark section, html.docmind-dark article {{
  background-color: {surface} !important;
  color: {text} !important;
  border-color: {border} !important;
}}
html.docmind-dark mark {{ background: {mark} !important; color: {text} !important; }}
"
            : "";

        return $@"
:root {{
  color-scheme: {(dark ? "dark" : "light")};
  --dm-bg: {bg};
  --dm-surface: {surface};
  --dm-text: {text};
  --dm-muted: {muted};
  --dm-primary: {primary};
  --dm-border: {border};
  --dm-code-bg: {codeBg};
  --dm-code-in: {codeIn};
  --dm-mark: {mark};
}}
html, body {{
  background: var(--dm-bg) !important;
  color: var(--dm-text) !important;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  line-height: 1.65 !important;
}}
h1, h2, h3, h4, h5, h6 {{ color: var(--dm-text) !important; line-height: 1.3 !important; }}
p, li, dd, dt, td, th, span, div {{ color: inherit !important; }}
a {{ color: var(--dm-primary) !important; }}
a.cite {{ color: var(--dm-primary) !important; font-weight: 600; }}
code, pre, kbd, samp {{
  background: var(--dm-code-bg) !important;
  color: var(--dm-text) !important;
  border-color: var(--dm-border) !important;
}}
pre {{ background: var(--dm-code-bg) !important; border: 1px solid var(--dm-border) !important; border-radius: 8px !important; padding: 12px !important; overflow: auto !important; }}
code {{ background: var(--dm-code-in) !important; border-radius: 4px !important; padding: 1px 5px !important; }}
pre code {{ background: transparent !important; padding: 0 !important; }}
blockquote {{
  border-left: 3px solid var(--dm-primary) !important;
  color: var(--dm-muted) !important;
  background: var(--dm-surface) !important;
}}
table {{ border-color: var(--dm-border) !important; width: 100% !important; border-collapse: collapse !important; }}
th, td {{ border: 1px solid var(--dm-border) !important; padding: 6px 10px !important; }}
th {{ background: var(--dm-surface) !important; }}
img {{ max-width: 100% !important; height: auto !important; border-radius: 6px !important; }}
mark {{ background: var(--dm-mark) !important; color: var(--dm-text) !important; }}
hr {{ border: none !important; border-top: 1px solid var(--dm-border) !important; }}
.card, .panel, .box, section, article {{ background: var(--dm-surface) !important; color: var(--dm-text) !important; }}
{darkBlock}

html, body {{ overflow-x: hidden !important; }}
html body * {{ position: static !important; }}
";
    }

    /// <summary>主题切换后热更新阅读区（不重载页面），并同步 WebView 底色。</summary>
    public void RefreshTheme()
    {
        try
        {
            SetResourceReference(BackgroundProperty, "SurfaceBrush");
            var isDark = IsAppDarkTheme();
            var scheme = isDark ? "dark" : "light";
            var cls = isDark ? "docmind-dark" : "docmind-light";
            if (_web?.WebView is { } web)
            {
                var c = isDark ? System.Drawing.Color.FromArgb(28, 28, 30) : System.Drawing.Color.White;
                web.DefaultBackgroundColor = c;
                if (web.CoreWebView2 is { } core)
                {
                    var themeJs =
                        "(function(){try{" +
                        "var r=document.documentElement;r.classList.remove('docmind-dark','docmind-light');" +
                        $"r.classList.add('{cls}');r.style.colorScheme='{scheme}';" +
                        $"var s=document.getElementById('docmind-theme');if(s){{s.textContent={System.Text.Json.JsonSerializer.Serialize(BuildThemeCss(isDark))};}}" +
                        "var m=document.querySelector('meta[name=color-scheme]');if(m)m.setAttribute('content','" + scheme + "');" +
                        "}catch(e){}})();";
                    _ = core.ExecuteScriptAsync(themeJs);
                }
            }
            if (_fallback is not null)
            {
                _fallback.Foreground = (Brush)(System.Windows.Application.Current?.FindResource("TextPrimaryBrush") ?? Brushes.Black);
            }
        }
        catch
        {
            // 主题热更新失败不影响已渲染内容
        }
    }

    private void ShowChild(UIElement child)
    {
        if (_root.Children.Count == 1 && ReferenceEquals(_root.Children[0], child))
        {
            return;
        }
        _root.Children.Clear();
        _root.Children.Add(child);
    }

    private void ShowFallback(string text)
    {
        _fallback ??= new TextBlock
        {
            TextWrapping = TextWrapping.Wrap,
            FontFamily = new FontFamily("Consolas, Microsoft YaHei"),
            FontSize = 12,
            Margin = new Thickness(12),
        };
        _fallback.Text = "(HTML 渲染不可用，以下为源码)\n\n" + text;
        // 降级文本同样限高：源码可能极长，无约束会撑出对话流溢出到输入区
        _fallback.MaxHeight = 240;
        var scroll = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            Content = _fallback,
        };
        ShowChild(scroll);
        Height = 240;
        MaxHeight = 240;
        ClipToBounds = true;
    }

    private void Cleanup()
    {
        // WebView2.Dispose 在 Unloaded（导航切页）时同步调用会卡死 UI 线程：
        // 浏览器子进程回收是阻塞的。推迟到后台线程释放，立即清空引用即可。
        var oldWeb = _web;
        _web = null;
        if (oldWeb?.WebView is { } web)
        {
            System.Threading.ThreadPool.QueueUserWorkItem(_ =>
            {
                try { web.Dispose(); } catch { /* 忽略 */ }
            });
        }
        _rendered = false;
        _loadedHtml = null;
        _isLoaded = false;
        // 复位到壳，下次 Loaded 时按最新状态重新渲染
        _root.Children.Clear();
        _root.Children.Add(_shell);
    }

    /// <summary>仅测试/内部用：当前已加载的整页 HTML。</summary>
    public string? LoadedHtml => _loadedHtml;

    /// <summary>包一层，避免直接引用 WebView2 类型暴露在测试断言外。</summary>
    private sealed class WebView2Provider
    {
        public Microsoft.Web.WebView2.Wpf.WebView2 WebView { get; }
        public WebView2Provider(Microsoft.Web.WebView2.Wpf.WebView2 webView) => WebView = webView;
    }
}
