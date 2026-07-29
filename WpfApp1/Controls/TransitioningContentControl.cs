using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Animation;

namespace DocMind.Controls;

/// <summary>
/// 内容切换时带淡入过渡效果的 ContentControl。
/// 每当 Content 变化时，新内容从 Opacity=0 淡入到 1。
/// </summary>
public class TransitioningContentControl : ContentControl
{
    private Storyboard? _fadeIn;

    static TransitioningContentControl()
    {
        DefaultStyleKeyProperty.OverrideMetadata(
            typeof(TransitioningContentControl),
            new FrameworkPropertyMetadata(typeof(TransitioningContentControl)));
    }

    protected override void OnContentChanged(object oldContent, object newContent)
    {
        base.OnContentChanged(oldContent, newContent);

        // 确保控件已加载完毕再运行动画
        if (!IsLoaded)
        {
            Loaded += OnLoadedForAnimation;
            return;
        }

        BeginFadeIn();
    }

    private void OnLoadedForAnimation(object sender, RoutedEventArgs e)
    {
        Loaded -= OnLoadedForAnimation;
        BeginFadeIn();
    }

    private void BeginFadeIn()
    {
        if (_fadeIn == null)
        {
            var anim = new DoubleAnimation
            {
                From = 0.0,
                To = 1.0,
                Duration = new Duration(System.TimeSpan.FromMilliseconds(250)),
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
            };
            Storyboard.SetTargetProperty(anim, new PropertyPath(OpacityProperty));
            _fadeIn = new Storyboard();
            _fadeIn.Children.Add(anim);
        }

        Opacity = 0.0;
        _fadeIn.Begin(this);
    }
}
