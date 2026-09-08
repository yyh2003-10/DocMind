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
        // 若系统关闭了动画效果（无障碍设置/远程桌面等），直接置 1.0 避免透明白屏
        if (!SystemParameters.ClientAreaAnimation)
        {
            Opacity = 1.0;
            return;
        }

        try
        {
            // 停止可能正在运行的旧动画，避免时钟冲突
            _fadeIn?.Stop(this);
            _fadeIn?.Remove(this);

            var anim = new DoubleAnimation
            {
                From = 0.0,
                To = 1.0,
                Duration = new Duration(System.TimeSpan.FromMilliseconds(200)),
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
                FillBehavior = FillBehavior.Stop,
            };
            Storyboard.SetTargetProperty(anim, new PropertyPath(OpacityProperty));

            _fadeIn = new Storyboard();
            _fadeIn.Children.Add(anim);
            _fadeIn.Completed += (_, _) =>
            {
                _fadeIn?.Remove(this);
                Opacity = 1.0;
            };

            Opacity = 0.0;
            _fadeIn.Begin(this, isControllable: true);
        }
        catch
        {
            // 任何动画异常发生时，降级保证界面立即可见
            Opacity = 1.0;
        }
    }
}
