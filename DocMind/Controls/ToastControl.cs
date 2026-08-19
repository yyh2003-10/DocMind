using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using DocMind.Models;

namespace DocMind.Controls
{
    /// <summary>
    /// Toast 通知弹出层控件。在屏幕右下角悬浮堆叠显示通知条目，
    /// 每条自动 3-5 秒淡出，支持手动点击关闭。
    /// </summary>
    public class ToastControl : ContentControl
    {
        private readonly StackPanel _panel;

        public ToastControl()
        {
            _panel = new StackPanel
            {
                Orientation = Orientation.Vertical,
                HorizontalAlignment = HorizontalAlignment.Right,
                VerticalAlignment = VerticalAlignment.Bottom,
                Margin = new Thickness(0, 0, 16, 16),
            };
            Content = _panel;
            Focusable = false;
            IsTabStop = false;
            HorizontalAlignment = HorizontalAlignment.Right;
            VerticalAlignment = VerticalAlignment.Bottom;
            MaxWidth = 380;
        }

        /// <summary>添加一条通知并显示。</summary>
        public void Show(ToastNotification notification)
        {
            var item = CreateToastItem(notification);
            _panel.Children.Add(item);

            // 限制最多同时展示 4 条，超出时移出最早的一条
            if (_panel.Children.Count > 4)
            {
                _panel.Children.RemoveAt(0);
            }
        }

        private FrameworkElement CreateToastItem(ToastNotification notification)
        {
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
                FontSize = 15,
                FontWeight = FontWeights.Bold,
                Foreground = accent,
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 8, 0),
            });

            var textStack = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            if (!string.IsNullOrWhiteSpace(notification.Title))
            {
                textStack.Children.Add(new TextBlock
                {
                    Text = notification.Title,
                    FontSize = 12,
                    FontWeight = FontWeights.SemiBold,
                    Foreground = textColor,
                    Margin = new Thickness(0, 0, 0, 2),
                });
            }
            textStack.Children.Add(new TextBlock
            {
                Text = notification.Message,
                FontSize = 12,
                Foreground = textColor,
                TextWrapping = TextWrapping.Wrap,
                MaxWidth = 280,
            });
            stack.Children.Add(textStack);

            var closeBtn = new TextBlock
            {
                Text = "✕",
                FontSize = 11,
                Foreground = new SolidColorBrush(Color.FromRgb(160, 174, 192)),
                VerticalAlignment = VerticalAlignment.Top,
                Cursor = System.Windows.Input.Cursors.Hand,
                Margin = new Thickness(8, 2, 0, 0),
            };

            var inner = new Grid();
            inner.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            inner.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            Grid.SetColumn(stack, 0);
            Grid.SetColumn(closeBtn, 1);
            inner.Children.Add(stack);
            inner.Children.Add(closeBtn);

            var border = new Border
            {
                Child = inner,
                Background = bg,
                BorderBrush = accent,
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(10, 8, 10, 8),
                Margin = new Thickness(0, 6, 0, 0),
                MaxWidth = 350,
                HorizontalAlignment = HorizontalAlignment.Right,
                Effect = new System.Windows.Media.Effects.DropShadowEffect
                {
                    BlurRadius = 10,
                    ShadowDepth = 3,
                    Color = Color.FromArgb(0x22, 0, 0, 0),
                    Opacity = 0.15,
                    RenderingBias = System.Windows.Media.Effects.RenderingBias.Performance,
                },
            };

            void Dismiss()
            {
                var fadeOut = new DoubleAnimation(1, 0, new Duration(System.TimeSpan.FromMilliseconds(250)));
                fadeOut.Completed += (_, _) => _panel.Children.Remove(border);
                border.BeginAnimation(UIElement.OpacityProperty, fadeOut);
            }

            closeBtn.MouseDown += (_, _) => Dismiss();

            // 自动淡出定时器
            var duration = notification.DurationMs > 0 ? notification.DurationMs : 3500;
            var timer = new System.Timers.Timer(duration) { AutoReset = false };
            timer.Elapsed += (_, _) =>
            {
                Dispatcher.Invoke(Dismiss);
                timer.Dispose();
            };
            timer.Start();

            // 淡入动画
            border.Opacity = 0;
            var fadeIn = new DoubleAnimation(0, 1, new Duration(System.TimeSpan.FromMilliseconds(250)))
            {
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
            };
            border.BeginAnimation(UIElement.OpacityProperty, fadeIn);

            return border;
        }
    }
}
