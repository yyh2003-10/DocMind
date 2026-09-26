using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace DocMind.Controls;

/// <summary>
/// 加载骨架屏控件：显示一个脉冲动画的圆角矩形占位块。
/// 在数据加载完成前展示，让用户感知内容即将出现。
/// </summary>
public class SkeletonControl : Control
{
    static SkeletonControl()
    {
        DefaultStyleKeyProperty.OverrideMetadata(
            typeof(SkeletonControl),
            new FrameworkPropertyMetadata(typeof(SkeletonControl)));
    }

    /// <summary>占位块圆角半径。</summary>
    public CornerRadius CornerRadius
    {
        get => (CornerRadius)GetValue(CornerRadiusProperty);
        set => SetValue(CornerRadiusProperty, value);
    }
    public static readonly DependencyProperty CornerRadiusProperty =
        DependencyProperty.Register(nameof(CornerRadius), typeof(CornerRadius),
            typeof(SkeletonControl), new PropertyMetadata(new CornerRadius(6)));

    /// <summary>占位块颜色（动画会作用于此 brush 的 Color）。</summary>
    public Brush SkeletonBrush
    {
        get => (Brush)GetValue(SkeletonBrushProperty);
        set => SetValue(SkeletonBrushProperty, value);
    }
    public static readonly DependencyProperty SkeletonBrushProperty =
        DependencyProperty.Register(nameof(SkeletonBrush), typeof(Brush),
            typeof(SkeletonControl), new PropertyMetadata(null));

    /// <summary>脉冲动画高亮色。</summary>
    public Brush HighlightBrush
    {
        get => (Brush)GetValue(HighlightBrushProperty);
        set => SetValue(HighlightBrushProperty, value);
    }
    public static readonly DependencyProperty HighlightBrushProperty =
        DependencyProperty.Register(nameof(HighlightBrush), typeof(Brush),
            typeof(SkeletonControl), new PropertyMetadata(null));

    /// <summary>动画低色（默认浅灰；OnApplyTemplate 时若主题提供 SkeletonFromColor 则覆盖，
    /// 避免暗色主题下骨架屏发亮）。</summary>
    public Color FromColor { get; set; } = Color.FromRgb(226, 232, 240);
    /// <summary>动画高色（默认更浅；主题提供 SkeletonToColor 时覆盖）。</summary>
    public Color ToColor { get; set; } = Color.FromRgb(247, 250, 252);

    // 内部可动画的 brush（避免使用冻结的共享默认值）
    private SolidColorBrush? _animBrush;

    public override void OnApplyTemplate()
    {
        base.OnApplyTemplate();
        ResolveThemeColors();
        StartPulseAnimation();
    }

    private void ResolveThemeColors()
    {
        if (TryFindResource("SkeletonFromColor") is Color from)
        {
            FromColor = from;
        }
        if (TryFindResource("SkeletonToColor") is Color to)
        {
            ToColor = to;
        }
    }

    private void StartPulseAnimation()
    {
        _animBrush = new SolidColorBrush(FromColor);

        // 系统关闭动画时回退静态呈现：直接落在低色，不做脉冲
        if (!SystemParameters.ClientAreaAnimation)
        {
            SkeletonBrush = _animBrush;
            return;
        }

        var pulseMs = TryFindPulseMs();
        var half = new Duration(System.TimeSpan.FromMilliseconds(pulseMs / 2.0));
        var easeInOut = new QuadraticEase { EasingMode = EasingMode.EaseInOut };

        // 脉冲 = 高亮层 Opacity 0→1→0 往返、全程 EaseInOut，柔和不刺眼。
        // 用 Opacity 而非 ColorAnimation：WPF 的 ColorAnimation 不支持 EasingFunction，且 Opacity 动画成本最低
        var pulse = new DoubleAnimation
        {
            From = 0,
            To = 1,
            Duration = half,
            AutoReverse = true,
            RepeatBehavior = RepeatBehavior.Forever,
            EasingFunction = easeInOut,
        };

        var highlight = new SolidColorBrush(ToColor) { Opacity = 0 };
        highlight.BeginAnimation(UIElement.OpacityProperty, pulse);

        // 同步到模板槽位：SkeletonBrush 为低色底层，HighlightBrush 为高亮覆盖层
        SkeletonBrush = _animBrush;
        HighlightBrush = highlight;
    }

    /// <summary>脉冲周期挂 SkeletonPulseMs(1400) Token，缺失时回退 1400。</summary>
    private static double TryFindPulseMs()
    {
        try
        {
            return Application.Current?.TryFindResource("SkeletonPulseMs") is double ms ? ms : 1400;
        }
        catch
        {
            return 1400;
        }
    }
}
