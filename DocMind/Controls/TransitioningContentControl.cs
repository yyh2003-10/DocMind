using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace DocMind.Controls;

/// <summary>
/// 内容切换：淡入 + 自底轻浮上（现代简约页面过渡，无白闪）。
/// </summary>
public class TransitioningContentControl : ContentControl
{
    private Storyboard? _enter;

    static TransitioningContentControl()
    {
        DefaultStyleKeyProperty.OverrideMetadata(
            typeof(TransitioningContentControl),
            new FrameworkPropertyMetadata(typeof(TransitioningContentControl)));
    }

    protected override void OnContentChanged(object oldContent, object newContent)
    {
        base.OnContentChanged(oldContent, newContent);

        if (!IsLoaded)
        {
            Loaded += OnLoadedForAnimation;
            return;
        }

        BeginEnter();
    }

    private void OnLoadedForAnimation(object sender, RoutedEventArgs e)
    {
        Loaded -= OnLoadedForAnimation;
        BeginEnter();
    }

    private void BeginEnter()
    {
        if (!SystemParameters.ClientAreaAnimation)
        {
            Opacity = 1.0;
            RenderTransform = null;
            return;
        }

        try
        {
            _enter?.Stop(this);
            _enter?.Remove(this);

            var translate = new TranslateTransform(0, 8);
            RenderTransform = translate;
            RenderTransformOrigin = new Point(0.5, 0.5);

            var fade = new DoubleAnimation
            {
                From = 0.0,
                To = 1.0,
                Duration = new Duration(TimeSpan.FromMilliseconds(220)),
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
                FillBehavior = FillBehavior.Stop,
            };
            var rise = new DoubleAnimation
            {
                From = 8.0,
                To = 0.0,
                Duration = new Duration(TimeSpan.FromMilliseconds(220)),
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
                FillBehavior = FillBehavior.Stop,
            };

            _enter = new Storyboard();
            Storyboard.SetTarget(fade, this);
            Storyboard.SetTargetProperty(fade, new PropertyPath(OpacityProperty));
            Storyboard.SetTarget(rise, translate);
            Storyboard.SetTargetProperty(rise, new PropertyPath(TranslateTransform.YProperty));
            _enter.Children.Add(fade);
            _enter.Children.Add(rise);
            _enter.Completed += (_, _) =>
            {
                _enter?.Remove(this);
                Opacity = 1.0;
            };

            Opacity = 0.0;
            _enter.Begin(this, isControllable: true);
        }
        catch
        {
            Opacity = 1.0;
            RenderTransform = null;
        }
    }
}
