using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Shapes;
using DocMind.Models;

namespace DocMind.Controls;

/// <summary>
/// Toast 通知弹出层控件。在屏幕右下角叠加显示通知条目，
/// 每条自动 3-5 秒淡出，支持手动关闭。
/// </summary>
public class ToastControl : Control
{
    private readonly System.Collections.Generic.List<ToastItem> _activeItems = new();

    static ToastControl()
    {
        DefaultStyleKeyProperty.OverrideMetadata(
            typeof(ToastControl),
            new FrameworkPropertyMetadata(typeof(ToastControl)));
    }

    /// <summary>添加一条通知并显示。</summary>
    public void Show(ToastNotification notification)
    {
        var item = new ToastItem(notification);
        _activeItems.Add(item);
        AddVisualChild(item);
        AddLogicalChild(item);

        item.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
        item.Arrange(new Rect(new Point(ActualWidth - item.DesiredSize.Width - 12,
                                        ActualHeight - 12 - GetTotalHeight()),
                              item.DesiredSize));

        // 启动自动关闭
        item.StartAutoDismiss(() => RemoveItem(item));
    }

    private void RemoveItem(ToastItem item)
    {
        _activeItems.Remove(item);
        RemoveVisualChild(item);
        RemoveLogicalChild(item);
        // 重新布局剩余项
        Rearrange();
    }

    private double GetTotalHeight()
    {
        double h = 0;
        foreach (var item in _activeItems)
            h += item.DesiredSize.Height + 8;
        return h;
    }

    private void Rearrange()
    {
        double y = ActualHeight - 12;
        for (int i = _activeItems.Count - 1; i >= 0; i--)
        {
            var item = _activeItems[i];
            y -= item.DesiredSize.Height;
            item.Arrange(new Rect(new Point(ActualWidth - item.DesiredSize.Width - 12, y), item.DesiredSize));
            y -= 8;
        }
    }

    protected override int VisualChildrenCount => _activeItems.Count;

    protected override Visual GetVisualChild(int index) => _activeItems[index];

    /// <summary>单条 Toast 条目（内部控件）。</summary>
    private class ToastItem : FrameworkElement
    {
        private readonly ToastNotification _notification;
        private readonly Border _border;
        private readonly Storyboard _fadeOut;
        private Action? _onDismiss;

        public ToastItem(ToastNotification notification)
        {
            _notification = notification;

            var (bg, icon) = notification.Type switch
            {
                ToastType.Success => (new SolidColorBrush(Color.FromRgb(240, 255, 244)), "✓"),
                ToastType.Warning => (new SolidColorBrush(Color.FromRgb(255, 251, 235)), "⚠"),
                ToastType.Error => (new SolidColorBrush(Color.FromRgb(255, 245, 245)), "✗"),
                _ => (new SolidColorBrush(Color.FromRgb(235, 248, 255)), "ℹ"),
            };

            var accent = notification.Type switch
            {
                ToastType.Success => new SolidColorBrush(Color.FromRgb(56, 161, 105)),
                ToastType.Warning => new SolidColorBrush(Color.FromRgb(214, 158, 46)),
                ToastType.Error => new SolidColorBrush(Color.FromRgb(229, 62, 62)),
                _ => new SolidColorBrush(Color.FromRgb(49, 130, 206)),
            };

            var textColor = new SolidColorBrush(Color.FromRgb(26, 32, 44));

            var stack = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(4, 0, 8, 0) };
            stack.Children.Add(new TextBlock
            {
                Text = icon,
                FontSize = 16,
                Foreground = accent,
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 8, 0),
            });
            stack.Children.Add(new TextBlock
            {
                Text = notification.Message,
                FontSize = 13,
                Foreground = textColor,
                TextWrapping = TextWrapping.Wrap,
                MaxWidth = 280,
                VerticalAlignment = VerticalAlignment.Center,
            });

            var closeBtn = new TextBlock
            {
                Text = "×",
                FontSize = 14,
                Foreground = new SolidColorBrush(Color.FromRgb(160, 174, 192)),
                VerticalAlignment = VerticalAlignment.Top,
                Cursor = System.Windows.Input.Cursors.Hand,
                Margin = new Thickness(8, 2, 0, 0),
            };
            closeBtn.MouseDown += (_, _) => _onDismiss?.Invoke();

            var inner = new Grid();
            inner.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            inner.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            Grid.SetColumn(stack, 0);
            Grid.SetColumn(closeBtn, 1);
            inner.Children.Add(stack);
            inner.Children.Add(closeBtn);

            _border = new Border
            {
                Child = inner,
                Background = bg,
                BorderBrush = accent,
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(8, 6, 8, 6),
                Effect = new System.Windows.Media.Effects.DropShadowEffect
                {
                    BlurRadius = 8,
                    ShadowDepth = 4,
                    Color = Color.FromArgb(0x1A, 0, 0, 0),
                    Opacity = 0.1,
                    RenderingBias = System.Windows.Media.Effects.RenderingBias.Performance,
                },
            };

            AddVisualChild(_border);
            AddLogicalChild(_border);

            // 淡入动画
            Opacity = 0;
            var fadeIn = new DoubleAnimation(0, 1, new Duration(System.TimeSpan.FromMilliseconds(300)))
            {
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
            };
            BeginAnimation(OpacityProperty, fadeIn);

            // 淡出动画（自动关闭前触发）
            _fadeOut = new Storyboard();
            var fadeOutAnim = new DoubleAnimation(1, 0, new Duration(System.TimeSpan.FromMilliseconds(400)));
            Storyboard.SetTargetProperty(fadeOutAnim, new PropertyPath(OpacityProperty));
            _fadeOut.Children.Add(fadeOutAnim);
            _fadeOut.Completed += (_, _) => _onDismiss?.Invoke();
        }

        public void StartAutoDismiss(Action onDismiss)
        {
            _onDismiss = onDismiss;
            var timer = new System.Timers.Timer(_notification.DurationMs) { AutoReset = false };
            timer.Elapsed += (_, _) =>
            {
                Dispatcher.Invoke(() => _fadeOut.Begin(this));
                timer.Dispose();
            };
            timer.Start();
        }

        protected override Size MeasureOverride(Size availableSize)
        {
            _border.Measure(new Size(320, double.PositiveInfinity));
            return _border.DesiredSize;
        }

        protected override Size ArrangeOverride(Size finalSize)
        {
            _border.Arrange(new Rect(default, finalSize));
            return finalSize;
        }

        protected override Visual GetVisualChild(int index) => _border;
        protected override int VisualChildrenCount => 1;
    }
}
