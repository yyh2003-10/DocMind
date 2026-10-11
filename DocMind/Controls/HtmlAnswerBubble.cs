namespace DocMind.Controls;

using System;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Runtime.InteropServices;
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
    private const double MaxExpandedHeight = 12000;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern int SetWindowRgn(IntPtr hWnd, IntPtr hRgn, bool redraw);

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateRectRgn(int left, int top, int right, int bottom);

    [DllImport("gdi32.dll")]
    private static extern bool DeleteObject(IntPtr hObject);

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool GetWindowRect(IntPtr hWnd, out NativeRect rect);
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
    // 原生裁剪缓存：区域未变时跳过 SetWindowRgn，避免滚动时逐帧重建 region 造成遮挡闪动
    private bool _hasNativeClip;
    private int _clipLeft, _clipTop, _clipRight, _clipBottom;
    private bool _clipEmpty;

    public HtmlAnswerBubble()
    {
        SetResourceReference(CornerRadiusProperty, "CornerLg");
        // 页面本身负责卡片底色和边框；外层保持透明，避免 Hwnd 裁剪期间露出灰色底块。
        Background = Brushes.Transparent;
        // WebView2 是原生 HWND；宿主会在滚动和布局时显式裁剪其原生窗口。
        ClipToBounds = true;
        BorderThickness = new Thickness(0);

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

    /// <summary>将 WebView2 原生窗口裁剪到对话列表的可见视口。
    /// 滚动时必须跟帧同步调用：慢一帧就会把已滚出视口的 HTML 盖到外层（遮挡闪动）。
    /// 区域未变化时跳过 SetWindowRgn，避免逐帧重建 region 触发重绘。</summary>
    public void UpdateNativeViewportClip(Rect viewportScreen)
    {
        if (_web?.WebView is not { IsLoaded: true } web || web.Handle == IntPtr.Zero)
        {
            return;
        }

        try
        {
            if (!GetWindowRect(web.Handle, out var nativeBounds))
            {
                return;
            }

            var bounds = new Rect(
                nativeBounds.Left,
                nativeBounds.Top,
                Math.Max(1, nativeBounds.Right - nativeBounds.Left),
                Math.Max(1, nativeBounds.Bottom - nativeBounds.Top));
            var visible = Rect.Intersect(bounds, viewportScreen);

            if (visible.IsEmpty || visible.Width <= 0 || visible.Height <= 0)
            {
                ApplyNativeClip(web.Handle, empty: true, 0, 0, 0, 0);
                return;
            }

            var left = (int)Math.Floor(visible.Left - bounds.Left);
            var top = (int)Math.Floor(visible.Top - bounds.Top);
            var right = (int)Math.Ceiling(visible.Right - bounds.Left);
            var bottom = (int)Math.Ceiling(visible.Bottom - bounds.Top);
            // 完全落在视口内：清掉裁剪区域（NULL region = 整窗可见），比设一个满窗 region 更少重绘
            if (left <= 0 && top <= 0 && right >= bounds.Width && bottom >= bounds.Height)
            {
                ApplyNativeClip(web.Handle, empty: false, 0, 0, 0, 0, clear: true);
                return;
            }

            ApplyNativeClip(web.Handle, empty: false, left, top, Math.Max(left + 1, right), Math.Max(top + 1, bottom));
        }
        catch
        {
            // 原生窗口在创建或销毁期间可能不可用，下一次布局会重试。
        }
    }

    /// <summary>应用原生窗口裁剪；区域与上次相同则直接返回（抑制滚动闪动）。</summary>
    private void ApplyNativeClip(IntPtr hwnd, bool empty, int left, int top, int right, int bottom, bool clear = false)
    {
        if (clear)
        {
            if (!_hasNativeClip)
            {
                return;
            }
            // NULL region：恢复整窗可见
            SetWindowRgn(hwnd, IntPtr.Zero, false);
            _hasNativeClip = false;
            _clipEmpty = false;
            return;
        }

        if (empty)
        {
            if (_hasNativeClip && _clipEmpty)
            {
                return;
            }
            var emptyRegion = CreateRectRgn(0, 0, 0, 0);
            if (emptyRegion != IntPtr.Zero)
            {
                // redraw=false：避免同步重绘造成闪动，由下一帧正常绘制
                if (SetWindowRgn(hwnd, emptyRegion, false) == 0)
                {
                    DeleteObject(emptyRegion);
                }
                else
                {
                    _hasNativeClip = true;
                    _clipEmpty = true;
                }
            }
            return;
        }

        if (_hasNativeClip && !_clipEmpty
            && _clipLeft == left && _clipTop == top && _clipRight == right && _clipBottom == bottom)
        {
            return;
        }

        var region = CreateRectRgn(left, top, right, bottom);
        if (region != IntPtr.Zero)
        {
            if (SetWindowRgn(hwnd, region, false) == 0)
            {
                DeleteObject(region);
            }
            else
            {
                _hasNativeClip = true;
                _clipEmpty = false;
                _clipLeft = left;
                _clipTop = top;
                _clipRight = right;
                _clipBottom = bottom;
            }
        }
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
    private static readonly Regex HtmlDocHintRegex = new(
        "<!DOCTYPE\\s+html|<html[\\s>]|<head[\\s>]|<body[\\s>]",
        RegexOptions.IgnoreCase | RegexOptions.Compiled);

    /// <summary>内容是否应走 HTML 体验气泡（而非 Markdown）。
    /// 不能只看开头：模型常在整页 HTML 前加一句说明，或包 ```html 围栏/注释。</summary>
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
        // 剥掉一层围栏后再判（有的输出是说明文字 + 围栏）
        var fenced = StripHtmlFence(s);
        if (!string.IsNullOrWhiteSpace(fenced) && fenced != s)
        {
            s = fenced.TrimStart();
        }
        if (s.StartsWith("<!DOCTYPE", StringComparison.OrdinalIgnoreCase)
            || s.StartsWith("<!doctype", StringComparison.Ordinal)
            || s.StartsWith("<html", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        // 完整 HTML 文档特征（允许前缀短文本/注释）
        if (HtmlDocHintRegex.IsMatch(content))
        {
            var idx = content.IndexOf("<!DOCTYPE", StringComparison.OrdinalIgnoreCase);
            if (idx < 0)
            {
                idx = content.IndexOf("<html", StringComparison.OrdinalIgnoreCase);
            }
            if (content.TrimStart().StartsWith("<", StringComparison.Ordinal) || (idx >= 0 && idx <= 400))
            {
                return true;
            }
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

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        _isLoaded = true;
        Refresh();
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
                    MinHeight = 120,
                    // Stretch：WebView 必须跟外层 Border 同高。Top + 无 Height 时 HwndHost
                    // 只占 DesiredHeight（约 MinHeight），Border 被撑高后下面会露出大片空白。
                    VerticalAlignment = VerticalAlignment.Stretch,
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
                // 先给一个稳定初值，页面测量完成后改为真实内容高度。
                Height = 400;
                web.Height = double.NaN;
                web.MinHeight = 120;
                web.VerticalAlignment = VerticalAlignment.Stretch;
                web.CoreWebView2.NavigateToString(WrapWithCsp(page));
                PushThemeToWebView();
                _ = LiftReadingWidthAsync(web.CoreWebView2);
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

    /// <summary>当前应用是否深色主题（读 IsDarkTheme 资源）。</summary>
    public static bool IsAppDarkTheme()
    {
        return Application.Current?.TryFindResource("IsDarkTheme") is bool flag && flag;
    }

    /// <summary>把应用 IsDarkTheme 推给 WebView2：
    /// 1) CDP 仿真 prefers-color-scheme（给写了 media query 的页面）；
    /// 2) 热更新 #docmind-theme 样式表（覆盖模型硬编码的浅色样式）；
    /// 3) 跑一遍颜色自适应，把残留的白底/黑字翻成主题色。</summary>
    private void PushThemeToWebView()
    {
        try
        {
            var core = _web?.WebView?.CoreWebView2;
            if (core is null) return;
            var isDark = IsAppDarkTheme();
            var mode = isDark ? "dark" : "light";
            var payload = $"{{\"features\":[{{\"name\":\"prefers-color-scheme\",\"value\":\"{mode}\"}}],\"media\":\"screen\"}}";
            _ = core.CallDevToolsProtocolMethodAsync("Emulation.setEmulatedMedia", payload);
            var themeJson = System.Text.Json.JsonSerializer.Serialize(BuildThemeCss(isDark));
            var js =
                "(function(){try{" +
                "var s=document.getElementById('docmind-theme');" +
                "if(s){s.textContent=" + themeJson + ";}" +
                "var m=document.querySelector('meta[name=\"color-scheme\"]');" +
                "if(m){m.setAttribute('content','" + mode + "');}" +
                "document.documentElement.style.colorScheme='" + mode + "';" +
                "if(" + (isDark ? "true" : "false") + "){window.__docmindAdaptDark&&window.__docmindAdaptDark();}" +
                "}catch(e){}})();";
            _ = core.ExecuteScriptAsync(js);
        }
        catch
        {
            // 主题仿真失败不影响渲染
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
        core.NavigationCompleted += (_, args) =>
        {
            // 页面加载完成后再推一次主题 + 跑深色自适应（覆盖模型硬编码浅色）
            Dispatcher.Invoke(() =>
            {
                PushThemeToWebView();
                if (IsAppDarkTheme() && _web?.WebView?.CoreWebView2 is { } c)
                {
                    _ = c.ExecuteScriptAsync("window.__docmindAdaptDark && window.__docmindAdaptDark();");
                }
            });
        };
        core.WebMessageReceived += OnWebMessageReceived;
    }

    /// <summary>高度自适应 + 引用桥脚本：页面创建前注入。</summary>
    private static async Task InjectBridgeAsync(CoreWebView2 core)
    {
        const string bridge = """
            (function(){
              function post(h){ try{ window.chrome.webview.postMessage({kind:'height',h:h}); }catch(e){} }
              function measure(){
                // 只取内容高度。
                // 严禁把 documentElement.scrollHeight/offsetHeight 并进 max——
                // html 元素总会撑满视口，短内容会被抬成「当前 WebView 高度」，
                // 气泡底部与来源列表之间就会空出一大块。
                var de = document.documentElement, body = document.body;
                if(!body) return 0;
                // 测量瞬间强制 html/body 回到内容高度，抵消模型 CSS 的 height/min-height:100%
                var restore = [];
                function forceAuto(el){
                  if(!el || !el.style) return;
                  restore.push([el, el.style.getPropertyValue('height'), el.style.getPropertyPriority('height'),
                    el.style.getPropertyValue('min-height'), el.style.getPropertyPriority('min-height')]);
                  el.style.setProperty('height','auto','important');
                  el.style.setProperty('min-height','0','important');
                }
                forceAuto(de); forceAuto(body);
                var h = Math.max(body.scrollHeight, body.offsetHeight);
                // 有绝对定位/折叠子节点时 scrollHeight 可能偏小，再扫一遍块级子节点
                var kids = body.children;
                for(var i=0;i<kids.length;i++){
                  var el = kids[i];
                  if(!el || !el.getBoundingClientRect) continue;
                  var r = el.getBoundingClientRect();
                  var bottom = r.top + r.height + (window.scrollY||0);
                  if(bottom > h) h = bottom;
                }
                for(var k=restore.length-1;k>=0;k--){
                  var rec = restore[k], el = rec[0];
                  if(rec[1]) el.style.setProperty('height', rec[1], rec[2]||'');
                  else el.style.removeProperty('height');
                  if(rec[3]) el.style.setProperty('min-height', rec[3], rec[4]||'');
                  else el.style.removeProperty('min-height');
                }
                return Math.ceil(h);
              }
              function report(){ post(measure()); }
              document.addEventListener('click', function(ev){
                var a = ev.target && ev.target.closest ? ev.target.closest('a.cite') : null;
                if(a){ ev.preventDefault(); var n = parseInt(a.getAttribute('data-n')||'0',10);
                  try{ window.chrome.webview.postMessage({kind:'cite', n:n}); }catch(e){} }
              }, true);
              // 深色自适应：把模型硬编码的白底/黑字翻到 DocMind 深色（保留高饱和强调色）
              function parseRgb(str){
                var m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/i.exec(str||'');
                return m ? [ +m[1], +m[2], +m[3] ] : null;
              }
              function isNearWhite(c){ return c && c[0]>=235 && c[1]>=235 && c[2]>=235; }
              function isNearBlack(c){ return c && c[0]<=55 && c[1]<=55 && c[2]<=65; }
              function isGrayish(c){
                if(!c) return true;
                var mx=Math.max(c[0],c[1],c[2]), mn=Math.min(c[0],c[1],c[2]);
                return (mx-mn) < 28;
              }
              function adaptDark(){
                try{
                  var els=[document.documentElement, document.body];
                  var all=document.body ? document.body.querySelectorAll('*') : [];
                  for(var i=0;i<all.length;i++) els.push(all[i]);
                  for(var j=0;j<els.length;j++){
                    var el=els[j];
                    if(!el || !el.style) continue;
                    var cs=getComputedStyle(el);
                    var bg=parseRgb(cs.backgroundColor);
                    var fg=parseRgb(cs.color);
                    if(isNearWhite(bg)){
                      // 白底卡片/页面 → 主题卡面
                      el.style.setProperty('background-color', (el===document.body||el===document.documentElement) ? '#0B0D11' : '#171C24', 'important');
                    }
                    if(isNearBlack(fg) && isGrayish(fg)){
                      el.style.setProperty('color', '#F4F7FB', 'important');
                    }
                  }
                  report();
                }catch(e){}
              }
              window.__docmindAdaptDark = adaptDark;
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
                // HTML 按正文高度在对话流中完整展开；外层 MessageScroll 统一滚动。
                Dispatcher.Invoke(() =>
                {
                    var clamped = Math.Clamp(h + 8, 120, MaxExpandedHeight);
                    if (double.IsNaN(clamped))
                    {
                        clamped = 400;
                    }
                    Height = clamped;
                    if (_web?.WebView is { } w)
                    {
                        w.MinHeight = 0;
                        w.VerticalAlignment = VerticalAlignment.Stretch;
                    }
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

    /// <summary>按 AllowCdn 下发 CSP meta，并注入 DocMind 深浅色主题 CSS（color-scheme + 变量覆盖）。</summary>
    private string WrapWithCsp(string page)
    {
        var csp = AllowCdn
            ? "default-src 'none'; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; img-src 'self' 'unsafe-inline' https: data:; font-src https: data:; connect-src https:; frame-src 'none'; object-src 'none'; base-uri 'none'"
            : "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' 'unsafe-inline' data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'";
        var isDark = IsAppDarkTheme();
        var scheme = isDark ? "dark" : "light";
        var meta =
            $"<meta http-equiv=\"Content-Security-Policy\" content=\"{csp}\">" +
            $"<meta name=\"color-scheme\" content=\"{scheme}\">" +
            $"<style id=\"docmind-theme\">{BuildThemeCss(isDark)}</style>";
        // 正文自然撑高，禁止页内纵向滚动；滚动交由外层 MessageScroll 处理。
        // 注意：不要对 body>*/section 等强制 width:100%——会把 flex/grid 子项压成单字换行。
        var layoutCss = "<style id=\"docmind-layout\">html,body{height:auto!important;min-height:0!important;margin:0;width:100%;max-width:100%!important;box-sizing:border-box;overflow:visible!important;}" +
                      "body{border:1px solid rgba(128,128,128,.35);border-radius:12px;}" +
                      "table{max-width:100%!important;}" +
                      "img,svg,canvas,video{max-width:100%!important;}" +
                      "*,*::before,*::after{box-sizing:border-box;}</style>";
        // 在 <head> 或文档开头插入 CSP/主题；同时把 target=_blank 等外链点击交给桥拦截（NavigationStarting 已兜底）
        var idx = page.IndexOf("<head", StringComparison.OrdinalIgnoreCase);
        if (idx >= 0)
        {
            var headClose = page.IndexOf('>', idx);
            if (headClose >= 0)
            {
                return page.Insert(headClose + 1, meta + layoutCss);
            }
        }
        return meta + layoutCss + page;
    }

    /// <summary>
    /// 生成与 Theme.xaml / Theme.Dark.xaml 对齐的阅读区主题 CSS。
    /// 模型 HTML 常硬编码浅色；这里用变量 + 关键选择器覆盖，保证气泡与主界面亮暗一致。
    /// </summary>
    public static string BuildThemeCss(bool dark)
    {
        // 与 Styles/Theme.xaml / Theme.Dark.xaml 关键色对齐
        var bg = dark ? "#0B0D11" : "#FBFCFE";
        var surface = dark ? "#161A21" : "#F4F6FA";
        var card = dark ? "#171C24" : "#FFFFFF";
        var text = dark ? "#F4F7FB" : "#1D1D1F";
        var muted = dark ? "#A6B0BF" : "#8E8E93";
        var faint = dark ? "#7E8A9B" : "#AEAEB2";
        var primary = dark ? "#818CF8" : "#4F46E5";
        var border = dark ? "#2A313C" : "#D9E0EA";
        var codeBg = dark ? "#161B22" : "#F6F6F8";
        var codeIn = dark ? "#23272E" : "#ECECF0";
        var mark = dark ? "#5B4B1A" : "#FEF3C7";
        var scheme = dark ? "dark" : "light";

        return $@":root {{
  color-scheme: {scheme};
  --dm-bg: {bg};
  --dm-surface: {surface};
  --dm-card: {card};
  --dm-text: {text};
  --dm-muted: {muted};
  --dm-faint: {faint};
  --dm-primary: {primary};
  --dm-border: {border};
  --dm-code-bg: {codeBg};
  --dm-code-in: {codeIn};
  --dm-mark: {mark};
}}
html, body {{
  background: var(--dm-bg) !important;
  color: var(--dm-text) !important;
  color-scheme: {scheme};
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  line-height: 1.65 !important;
}}
h1, h2, h3, h4, h5, h6 {{ color: var(--dm-text) !important; line-height: 1.3 !important; }}
p, li, dd, dt {{ color: inherit; }}
a {{ color: var(--dm-primary) !important; }}
a.cite {{ color: var(--dm-primary) !important; font-weight: 600; }}
code, pre, kbd, samp {{
  background: var(--dm-code-bg) !important;
  color: var(--dm-text) !important;
  border-color: var(--dm-border) !important;
}}
pre {{
  background: var(--dm-code-bg) !important;
  border: 1px solid var(--dm-border) !important;
  border-radius: 8px !important;
  padding: 12px !important;
  overflow: auto !important;
}}
code {{
  background: var(--dm-code-in) !important;
  border-radius: 4px !important;
  padding: 1px 5px !important;
}}
pre code {{ background: transparent !important; padding: 0 !important; }}
blockquote {{
  border-left: 3px solid var(--dm-primary) !important;
  color: var(--dm-muted) !important;
  background: var(--dm-surface) !important;
  margin: 0.6em 0 !important;
  padding: 0.4em 0.9em !important;
}}
table {{ border-color: var(--dm-border) !important; width: 100% !important; border-collapse: collapse !important; }}
th, td {{ border: 1px solid var(--dm-border) !important; padding: 6px 10px !important; color: inherit; }}
th {{ background: var(--dm-surface) !important; color: var(--dm-text) !important; }}
img {{ max-width: 100% !important; height: auto !important; border-radius: 6px !important; }}
mark {{ background: var(--dm-mark) !important; color: var(--dm-text) !important; }}
hr {{ border: none !important; border-top: 1px solid var(--dm-border) !important; }}
.card, .panel, .box, .item, .feature, .step, .timeline-item {{
  background: var(--dm-card) !important;
  color: var(--dm-text) !important;
  border-color: var(--dm-border) !important;
}}
::selection {{ background: color-mix(in srgb, var(--dm-primary) 35%, transparent); }}
";
    }

    /// <summary>页面加载后撑开自带阅读窄栏（max-width），让 HTML 用满气泡宽度。
    /// 只处理 html/body 与阅读栏级别的容器（约 600–900px），不要遍历所有节点——
    /// 那会把 flex/grid 子项的 max-width 也抬掉，导致侧栏被压成单字换行。</summary>
    private static async Task LiftReadingWidthAsync(CoreWebView2 core)
    {
        try
        {
            await Task.Delay(30);
            const string js = """
                (function(){
                  function lift(el){
                    if(!el || !el.style) return;
                    try{
                      var cs = getComputedStyle(el);
                      var mw = parseFloat(cs.maxWidth);
                      if(cs.maxWidth && cs.maxWidth !== 'none' && !isNaN(mw) && mw >= 560 && mw <= 960){
                        el.style.setProperty('max-width','100%','important');
                      }
                    }catch(e){}
                  }
                  lift(document.documentElement);
                  lift(document.body);
                  var sels = 'main,article,.container,.wrapper,.content,.post,.article,.page,.inner,.markdown-body,.prose';
                  var all = document.body ? document.body.querySelectorAll(sels) : [];
                  for(var i=0;i<all.length;i++){ lift(all[i]); }
                })();
                """;
            await core.ExecuteScriptAsync(js);
        }
        catch
        {
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
        ShowChild(_fallback);
        Height = double.NaN;
    }

    private void Cleanup()
    {
        if (_web?.WebView is { } web)
        {
            try { web.Dispose(); } catch { }
        }
        _web = null;
        _rendered = false;
        _loadedHtml = null;
        _isLoaded = false;
        _hasNativeClip = false;
        _clipEmpty = false;
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
