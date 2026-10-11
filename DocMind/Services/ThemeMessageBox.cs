using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shell;

namespace DocMind.Services;

/// <summary>消息框按钮组合（对齐 System.Windows.MessageBoxButton）。</summary>
public enum ThemeMessageBoxButton
{
    OK,
    OKCancel,
    YesNo,
    YesNoCancel,
}

/// <summary>消息框语义类型：决定图标与强调色（对齐 ToastControl.ResolveKind）。</summary>
public enum ThemeMessageBoxImage
{
    None,
    Information,
    Question,
    Warning,
    Error,
}

/// <summary>用户点击结果（对齐 System.Windows.MessageBoxResult）。</summary>
public enum ThemeMessageBoxResult
{
    None = 0,
    OK,
    Cancel,
    Yes,
    No,
}

/// <summary>
/// 主题化消息框：替代系统原生 MessageBox，与 DocMind Design System 同源
/// （Card 卡片 + Border 描边 + DWM 圆角 + 语义色图标 + Primary/Secondary 按钮）。
/// 皮肤资源全部走 DynamicResource，深浅色主题自动跟随（与 MainWindow 同款
/// WindowStyle=None + WindowChrome 方案，圆角走 DWMWA_WINDOW_CORNER_PREFERENCE）。
/// 静态 API 与 System.Windows.MessageBox.Show 签名对齐，便于全局替换。
/// </summary>
public static class ThemeMessageBox
{
    /// <summary>默认按钮：YesNo 为 No、OKCancel 为 Cancel，回车/关闭不误触破坏性确认。</summary>
    private static ThemeMessageBoxResult DefaultResult(ThemeMessageBoxButton buttons) => buttons switch
    {
        ThemeMessageBoxButton.YesNo => ThemeMessageBoxResult.No,
        ThemeMessageBoxButton.YesNoCancel => ThemeMessageBoxResult.Cancel,
        ThemeMessageBoxButton.OKCancel => ThemeMessageBoxResult.Cancel,
        _ => ThemeMessageBoxResult.OK,
    };

    public static ThemeMessageBoxResult Show(
        string message,
        string title = "DocMind",
        ThemeMessageBoxButton button = ThemeMessageBoxButton.OK,
        ThemeMessageBoxImage image = ThemeMessageBoxImage.None,
        Window? owner = null)
        => ShowCore(owner, message, title, button, image, DefaultResult(button));

    /// <summary>破坏性确认用：指定默认结果与「是」按钮文案（如「删除」）。</summary>
    public static ThemeMessageBoxResult Show(
        string message,
        string title,
        ThemeMessageBoxButton button,
        ThemeMessageBoxImage image,
        ThemeMessageBoxResult defaultResult,
        string? yesLabelOverride = null)
        => ShowCore(owner: null, message, title, button, image, defaultResult, yesLabelOverride);

    private static ThemeMessageBoxResult ShowCore(
        Window? owner,
        string message,
        string title,
        ThemeMessageBoxButton button,
        ThemeMessageBoxImage image,
        ThemeMessageBoxResult defaultResult,
        string? yesLabelOverride = null)
    {
        var dlg = new ThemeMessageBoxWindow(message, title, button, image, defaultResult, yesLabelOverride)
        {
            Owner = owner ?? Application.Current?.MainWindow,
        };
        dlg.ShowDialog();
        return dlg.Result;
    }
}

/// <summary>主题化消息框窗口本体（纯代码构建，避免新增 XAML 资源引用风险）。</summary>
public sealed class ThemeMessageBoxWindow : Window
{
    private const int DwmwaWindowCornerPreference = 33;
    private const int DwmwcpRound = 2;

    /// <summary>点击结果（ShowDialog 返回后读取）。</summary>
    public ThemeMessageBoxResult Result { get; private set; } = ThemeMessageBoxResult.None;

    public ThemeMessageBoxWindow(
        string message,
        string title,
        ThemeMessageBoxButton button,
        ThemeMessageBoxImage image,
        ThemeMessageBoxResult defaultResult,
        string? yesLabelOverride)
    {
        Title = title;
        Width = 420;
        MinWidth = 380;
        MaxWidth = 560;
        MinHeight = 150;
        MaxHeight = 560;
        SizeToContent = SizeToContent.Height;
        ResizeMode = ResizeMode.NoResize;
        WindowStyle = WindowStyle.None;
        AllowsTransparency = false;
        ShowInTaskbar = false;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = TryBrush("CardBrush") ?? Brushes.White;
        Foreground = TryBrush("TextPrimaryBrush") ?? Brushes.Black;
        FontFamily = new FontFamily("SF Pro Display, Segoe UI Variable Display, Segoe UI, Microsoft YaHei, sans-serif");
        UseLayoutRounding = true;
        SnapsToDevicePixels = true;

        // 与 MainWindow 同款自定义 Chrome：无边框 + 1px DWM 玻璃边（借 DWM 画阴影），圆角走 DWM 属性
        WindowChrome.SetWindowChrome(this, new WindowChrome
        {
            CaptionHeight = 0,
            ResizeBorderThickness = new Thickness(0),
            GlassFrameThickness = new Thickness(1),
            CornerRadius = new CornerRadius(0),
            UseAeroCaptionButtons = false,
        });

        var (accentBrush, lightBrush, iconGeometry) = ResolveImage(image);

        // ---------- 标题（无原生标题栏，标题作为卡片内文本呈现） ----------
        var titleBlock = new TextBlock
        {
            Text = title,
            FontSize = 16,
            FontWeight = FontWeights.SemiBold,
            Foreground = TryBrush("TextPrimaryBrush") ?? Brushes.Black,
            Margin = new Thickness(24, 20, 24, 0),
            TextTrimming = TextTrimming.CharacterEllipsis,
        };

        // ---------- 图标 + 正文 ----------
        var iconTile = new Border
        {
            Width = 36,
            Height = 36,
            CornerRadius = new CornerRadius(10),
            Background = lightBrush,
            VerticalAlignment = VerticalAlignment.Top,
            Child = iconGeometry is null
                ? null
                : new System.Windows.Shapes.Path
                {
                    Data = iconGeometry,
                    Width = 20,
                    Height = 20,
                    Stretch = Stretch.Uniform,
                    Stroke = accentBrush,
                    StrokeThickness = 1.6,
                    StrokeLineJoin = PenLineJoin.Round,
                    StrokeStartLineCap = PenLineCap.Round,
                    StrokeEndLineCap = PenLineCap.Round,
                    Fill = Brushes.Transparent,
                    VerticalAlignment = VerticalAlignment.Center,
                    HorizontalAlignment = HorizontalAlignment.Center,
                },
        };

        var textBlock = new TextBlock
        {
            Text = message,
            FontSize = 14,
            LineHeight = 22,
            TextWrapping = TextWrapping.Wrap,
            Foreground = TryBrush("TextPrimaryBrush") ?? Brushes.Black,
            VerticalAlignment = VerticalAlignment.Center,
        };

        var messageRow = new Grid
        {
            Margin = new Thickness(24, 14, 24, 0),
        };
        messageRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        messageRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        Grid.SetColumn(iconTile, 0);
        Grid.SetColumn(textBlock, 1);
        messageRow.Children.Add(iconTile);
        messageRow.Children.Add(textBlock);
        if (iconGeometry is not null)
        {
            iconTile.Margin = new Thickness(0, 0, 12, 0);
        }
        else
        {
            // 无语义图标（MessageBoxImage.None）：隐藏磁贴，正文占满宽度
            iconTile.Visibility = Visibility.Collapsed;
        }

        // 超长消息（如后端启动失败诊断）限高滚动，避免对话框超出屏幕
        var messageScroll = new ScrollViewer
        {
            Content = messageRow,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            MaxHeight = 320,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            VerticalAlignment = VerticalAlignment.Top,
        };

        // ---------- 按钮区（分隔线 + 右对齐，Yes 永远最右） ----------
        var separator = new Border
        {
            Height = 1,
            Background = TryBrush("BorderLightBrush") ?? TryBrush("BorderBrush"),
            Margin = new Thickness(24, 16, 24, 0),
        };

        var buttonPanel = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(24, 12, 24, 20),
        };

        void AddButton(string label, ThemeMessageBoxResult result, bool primary, bool isDefault, bool isCancel)
        {
            var btn = new Button
            {
                Content = label,
                MinWidth = 84,
                MinHeight = 34,
                Padding = new Thickness(18, 6, 18, 6),
                Margin = new Thickness(8, 0, 0, 0),
                Cursor = System.Windows.Input.Cursors.Hand,
                Style = primary ? TryStyle("PrimaryButtonStyle") : TryStyle("SecondaryButtonStyle"),
                IsDefault = isDefault,
                IsCancel = isCancel,
            };
            btn.Click += (_, _) =>
            {
                Result = result;
                Close();
            };
            buttonPanel.Children.Add(btn);
        }

        // Esc 等价按钮：YesNo 落「否」，其余落「取消」（与原生 MessageBox 安全侧一致）
        var cancelEquivalent = button == ThemeMessageBoxButton.YesNo
            ? ThemeMessageBoxResult.No
            : ThemeMessageBoxResult.Cancel;

        switch (button)
        {
            case ThemeMessageBoxButton.OK:
                AddButton("确定", ThemeMessageBoxResult.OK, primary: true, isDefault: true, isCancel: false);
                break;
            case ThemeMessageBoxButton.OKCancel:
                AddButton("取消", ThemeMessageBoxResult.Cancel, primary: false,
                    isDefault: defaultResult == ThemeMessageBoxResult.Cancel,
                    isCancel: cancelEquivalent == ThemeMessageBoxResult.Cancel);
                AddButton("确定", ThemeMessageBoxResult.OK, primary: true,
                    isDefault: defaultResult == ThemeMessageBoxResult.OK,
                    isCancel: false);
                break;
            case ThemeMessageBoxButton.YesNo:
                AddButton("否", ThemeMessageBoxResult.No, primary: false,
                    isDefault: defaultResult == ThemeMessageBoxResult.No,
                    isCancel: cancelEquivalent == ThemeMessageBoxResult.No);
                AddButton(yesLabelOverride ?? "是", ThemeMessageBoxResult.Yes, primary: true,
                    isDefault: defaultResult == ThemeMessageBoxResult.Yes,
                    isCancel: false);
                break;
            case ThemeMessageBoxButton.YesNoCancel:
                AddButton("取消", ThemeMessageBoxResult.Cancel, primary: false,
                    isDefault: defaultResult == ThemeMessageBoxResult.Cancel,
                    isCancel: true);
                AddButton("否", ThemeMessageBoxResult.No, primary: false,
                    isDefault: defaultResult == ThemeMessageBoxResult.No,
                    isCancel: false);
                AddButton("是", ThemeMessageBoxResult.Yes, primary: true,
                    isDefault: defaultResult == ThemeMessageBoxResult.Yes,
                    isCancel: false);
                break;
        }

        var body = new StackPanel();
        body.Children.Add(titleBlock);
        body.Children.Add(messageScroll);
        body.Children.Add(separator);
        body.Children.Add(buttonPanel);

        // 卡片根：Card 底 + 1px 语义描边 + CornerLg(8) 圆角，与 DWM 圆角对齐
        var card = new Border
        {
            Child = body,
            Background = TryBrush("CardBrush") ?? Brushes.White,
            BorderBrush = TryBrush("BorderBrush") ?? Brushes.LightGray,
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
        };

        Content = card;

        // Alt+F4 / 标题栏关闭（无 X 按钮，仅键盘路径）→ 落到默认安全结果，不误触破坏性确认
        Closing += (_, _) =>
        {
            if (Result == ThemeMessageBoxResult.None)
            {
                Result = defaultResult;
            }
        };
    }

    /// <summary>源就绪后启用 Win11 系统圆角（Win10 上 DwmSetWindowAttribute 失败则退回直角，不影响功能）。</summary>
    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        try
        {
            var hwnd = new WindowInteropHelper(this).Handle;
            if (hwnd == IntPtr.Zero)
            {
                return;
            }
            var corner = DwmwcpRound;
            NativeMethods.DwmSetWindowAttribute(
                hwnd,
                DwmwaWindowCornerPreference,
                ref corner,
                sizeof(int));
        }
        catch (DllNotFoundException)
        {
            // 旧系统无 dwmapi：直角边框，视觉降级但不报错
        }
    }

    /// <summary>语义类型 →（强调色、淡底、图标 Geometry），配色键与 ToastControl.ResolveKind 一致。</summary>
    private static (Brush accent, Brush light, Geometry? icon) ResolveImage(ThemeMessageBoxImage image) => image switch
    {
        ThemeMessageBoxImage.Warning => (TryBrush("WarningBrush") ?? Brushes.DarkOrange,
            TryBrush("WarningLightBrush") ?? new SolidColorBrush(Color.FromRgb(255, 244, 230)),
            TryGeometry("IconWarning")),
        ThemeMessageBoxImage.Error => (TryBrush("DangerBrush") ?? Brushes.IndianRed,
            TryBrush("DangerLightBrush") ?? new SolidColorBrush(Color.FromRgb(255, 236, 234)),
            TryGeometry("IconCross")),
        ThemeMessageBoxImage.Question => (TryBrush("PrimaryBrush") ?? Brushes.DodgerBlue,
            TryBrush("PrimaryLightBrush") ?? new SolidColorBrush(Color.FromRgb(238, 242, 255)),
            TryGeometry("IconChat")),
        ThemeMessageBoxImage.Information => (TryBrush("InfoBrush") ?? Brushes.SkyBlue,
            TryBrush("InfoLightBrush") ?? new SolidColorBrush(Color.FromRgb(230, 249, 255)),
            TryGeometry("IconLightbulb")),
        _ => (TryBrush("PrimaryBrush") ?? Brushes.DodgerBlue,
            TryBrush("SurfaceBrush") ?? Brushes.LightGray, null),
    };

    internal static Brush? TryBrush(string key) =>
        Application.Current?.TryFindResource(key) as Brush;

    internal static Geometry? TryGeometry(string key) =>
        Application.Current?.TryFindResource(key) as Geometry;

    internal static Style? TryStyle(string key) =>
        Application.Current?.TryFindResource(key) as Style;

    /// <summary>DWM 属性设置（复用 App.NativeMethods 的内部封装）。</summary>
    private static class NativeMethods
    {
        [DllImport("dwmapi.dll", PreserveSig = true)]
        internal static extern int DwmSetWindowAttribute(
            IntPtr hwnd, int dwAttribute, ref int pvAttribute, int cbAttribute);
    }
}
