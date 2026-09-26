using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
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

        private static double TryFindDouble(string key, double fallback)
        {
            try
            {
                return Application.Current?.TryFindResource(key) is double v ? v : fallback;
            }
            catch
            {
                return fallback;
            }
        }

        private static Brush? TryFindBrush(string key)
        {
            try
            {
                return Application.Current?.TryFindResource(key) as Brush;
            }
            catch
            {
                return null;
            }
        }

        /// <summary>语义分类 →（淡底 Token 键、强调色 Token 键、图标资源键）三元组。</summary>
        private static (string BgKey, string AccentKey, string IconKey) ResolveKind(ToastType type) =>
            type switch
            {
                ToastType.Success => ("SuccessLightBrush", "SuccessBrush", "IconCheck"),
                ToastType.Warning => ("WarningLightBrush", "WarningBrush", "IconWarning"),
                ToastType.Error => ("DangerLightBrush", "DangerBrush", "IconCross"),
                _ => ("InfoLightBrush", "InfoBrush", "IconLightbulb"),
            };

        private FrameworkElement CreateToastItem(ToastNotification notification)
        {
            var (bgKey, accentKey, iconKey) = ResolveKind(notification.Type);

            var bg = TryFindBrush(bgKey)
                     ?? new SolidColorBrush(Color.FromRgb(32, 48, 36));
            var accent = TryFindBrush(accentKey)
                         ?? new SolidColorBrush(Color.FromRgb(129, 140, 248));
            var textColor = TryFindBrush("TextPrimaryBrush")
                            ?? new SolidColorBrush(Color.FromRgb(245, 245, 247));
            var mutedText = TryFindBrush("TextTertiaryBrush")
                            ?? new SolidColorBrush(Color.FromRgb(142, 142, 147));
            var cardBrush = TryFindBrush("CardBrush")
                            ?? new SolidColorBrush(Color.FromRgb(28, 28, 30));
            var borderBrush = TryFindBrush("ElevatedBorderBrush")
                              ?? TryFindBrush("BorderBrush")
                              ?? new SolidColorBrush(Color.FromRgb(56, 56, 58));

            var iconGeometry = (Geometry)Application.Current.FindResource(iconKey);

            var stack = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(4, 0, 8, 0) };
            stack.Children.Add(new System.Windows.Shapes.Path
            {
                Data = iconGeometry,
                Width = 16,
                Height = 16,
                Stroke = accent,
                StrokeThickness = 1.5,
                Stretch = Stretch.Uniform,
                StrokeLineJoin = PenLineJoin.Round,
                StrokeStartLineCap = PenLineCap.Round,
                StrokeEndLineCap = PenLineCap.Round,
                Fill = Brushes.Transparent,
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

            var closeIconGeometry = (Geometry)Application.Current.FindResource("IconClose");
            var closeBtn = new System.Windows.Shapes.Path
            {
                Data = closeIconGeometry,
                Width = 12,
                Height = 12,
                Stroke = mutedText,
                StrokeThickness = 1.5,
                Stretch = Stretch.Uniform,
                StrokeLineJoin = PenLineJoin.Round,
                StrokeStartLineCap = PenLineCap.Round,
                StrokeEndLineCap = PenLineCap.Round,
                Fill = Brushes.Transparent,
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

            // 左侧语义色指示条 + 内容区：先组装 rootGrid，再一次性挂到 border.Child，
            // 避免同一元素先后属于两个逻辑父级（InvalidOperationException）
            var accentBar = new Border
            {
                Width = 3,
                Background = accent,
                CornerRadius = new CornerRadius(1.5, 0, 0, 1.5),
                VerticalAlignment = VerticalAlignment.Stretch,
                Margin = new Thickness(0),
            };
            var rootGrid = new Grid();
            rootGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            rootGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            Grid.SetColumn(accentBar, 0);
            Grid.SetColumn(inner, 1);
            rootGrid.Children.Add(accentBar);
            rootGrid.Children.Add(inner);

            var border = new Border
            {
                Child = rootGrid,
                // 卡片底用主题 Card，左侧 3px 品牌/语义色条表达类型
                Background = cardBrush,
                BorderBrush = borderBrush,
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(10),
                Padding = new Thickness(0, 8, 10, 8),
                Margin = new Thickness(0, 6, 0, 0),
                MaxWidth = 350,
                HorizontalAlignment = HorizontalAlignment.Right,
                Effect = new System.Windows.Media.Effects.DropShadowEffect
                {
                    BlurRadius = 12,
                    ShadowDepth = 2,
                    Color = Color.FromArgb(0x33, 0, 0, 0),
                    Opacity = 0.18,
                    RenderingBias = System.Windows.Media.Effects.RenderingBias.Performance,
                },
            };

            // 用 DispatcherTimer（UI 线程）而非 System.Timers.Timer + Invoke，
            // 避免应用关闭时同步 Invoke 抛 TaskCanceledException
            var duration = notification.DurationMs > 0 ? notification.DurationMs : 3500;
            var timer = new DispatcherTimer
            {
                Interval = System.TimeSpan.FromMilliseconds(duration),
            };
            var dismissed = false;
            void Dismiss()
            {
                if (dismissed) return;
                dismissed = true;
                timer.Stop();
                var fadeOut = new DoubleAnimation(1, 0, new Duration(System.TimeSpan.FromMilliseconds(180)))
                {
                    EasingFunction = new CubicEase { EasingMode = EasingMode.EaseIn },
                };
                fadeOut.Completed += (_, _) =>
                {
                    if (_panel.Children.Contains(border))
                    {
                        _panel.Children.Remove(border);
                    }
                };
                border.BeginAnimation(UIElement.OpacityProperty, fadeOut);
            }

            timer.Tick += (_, _) => Dismiss();
            timer.Start();

            closeBtn.MouseDown += (_, _) => Dismiss();

            // 入场：自底上移 8px + 淡入 + 轻 scale(0.97→1)，时长挂 MotionNormalMs(180)；系统关闭动画时即时静态呈现
            var motionNormal = System.TimeSpan.FromMilliseconds(TryFindDouble("MotionNormalMs", 180));
            if (SystemParameters.ClientAreaAnimation)
            {
                border.Opacity = 0;
                var scale = new ScaleTransform(0.97, 0.97);
                var translate = new TranslateTransform(0, 8);
                border.RenderTransformOrigin = new Point(1, 1);
                border.RenderTransform = new TransformGroup { Children = { scale, translate } };
                var fadeIn = new DoubleAnimation(0, 1, motionNormal)
                {
                    EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
                };
                var slideIn = new DoubleAnimation(8, 0, motionNormal)
                {
                    EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
                };
                var scaleUp = new DoubleAnimation(0.97, 1, motionNormal)
                {
                    EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
                };
                border.BeginAnimation(UIElement.OpacityProperty, fadeIn);
                border.BeginAnimation(TranslateTransform.YProperty, slideIn);
                border.BeginAnimation(ScaleTransform.ScaleXProperty, scaleUp);
                border.BeginAnimation(ScaleTransform.ScaleYProperty, scaleUp);
            }

            return border;
        }
    }
}
