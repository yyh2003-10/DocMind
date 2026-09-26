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
        // 页面切换只做可见性交换；淡入动画在 Content 重挂时可能中断并卡在 Opacity=0
        Opacity = 1.0;
        RenderTransform = null;
    }
}
