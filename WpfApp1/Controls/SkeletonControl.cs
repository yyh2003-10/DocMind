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

    /// <summary>动画低色（默认浅灰）。</summary>
    public Color FromColor { get; set; } = Color.FromRgb(226, 232, 240);
    /// <summary>动画高色（默认更浅）。</summary>
    public Color ToColor { get; set; } = Color.FromRgb(247, 250, 252);

    // 内部可动画的 brush（避免使用冻结的共享默认值）
    private SolidColorBrush? _animBrush;

    public override void OnApplyTemplate()
    {
        base.OnApplyTemplate();
        StartPulseAnimation();
    }

    private void StartPulseAnimation()
    {
        // 创建未冻结的可动画 brush 实例，绑定到模板根
        _animBrush = new SolidColorBrush(FromColor);

        var anim = new ColorAnimation
        {
            From = FromColor,
            To = ToColor,
            Duration = new Duration(System.TimeSpan.FromMilliseconds(1200)),
            AutoReverse = true,
            RepeatBehavior = RepeatBehavior.Forever,
        };

        _animBrush.BeginAnimation(SolidColorBrush.ColorProperty, anim);

        // 同步到 SkeletonBrush 供模板使用（模板用 {TemplateBinding SkeletonBrush}）
        SkeletonBrush = _animBrush;
    }
}
