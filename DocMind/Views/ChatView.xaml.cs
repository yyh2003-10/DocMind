using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Views;

public partial class ChatView : UserControl
{
    private ChatViewModel? _vm;

    public ChatView()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        DataContextChanged += OnDataContextChanged;
        PreviewKeyDown += ChatView_PreviewKeyDown;
    }

    private void ChatView_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape && _vm is { IsSourceDrawerOpen: true })
        {
            _vm.CloseSourceDrawerCommand.Execute(null);
            e.Handled = true;
        }
    }

    private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (_vm != null)
        {
            _vm.Messages.CollectionChanged -= Messages_CollectionChanged;
        }
        _vm = e.NewValue as ChatViewModel;
        if (_vm != null)
        {
            _vm.Messages.CollectionChanged += Messages_CollectionChanged;
        }
    }

    /// <summary>拖动抽屉左边缘的把手：向左拖变宽、向右拖变窄。
    /// 只改 ViewModel 属性（内部钳制到 240~720 并落盘），再由双向绑定回传更新抽屉实际宽度；
    /// 不直接改写布局容器的 Width，因此抽屉关闭时列宽仍能正常塌陷为 0。</summary>
    private void DrawerThumb_DragDelta(object sender, System.Windows.Controls.Primitives.DragDeltaEventArgs e)
    {
        if (_vm != null)
        {
            _vm.SourceDrawerWidth = _vm.SourceDrawerWidth - e.HorizontalChange;
        }
    }

    /// <summary>松手时落盘一次（拖动过程只改内存，避免逐帧执行含 DPAPI 加密的 Save 造成卡顿）。</summary>
    private void DrawerThumb_DragCompleted(object sender, System.Windows.Controls.Primitives.DragCompletedEventArgs e)
    {
        _vm?.PersistSourceDrawerWidth();
    }

    /// <summary>点击灵感快捷场景按钮，弹出提示词模板菜单。</summary>
    private void InspirationButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button btn && btn.ContextMenu != null)
        {
            btn.ContextMenu.PlacementTarget = btn;
            btn.ContextMenu.Placement = System.Windows.Controls.Primitives.PlacementMode.Top;
            btn.ContextMenu.IsOpen = true;
        }
    }

    /// <summary>点击 @知识库 锚定徽章，弹出知识库集合多选面板。</summary>
    private void KnowledgeBadge_Click(object sender, MouseButtonEventArgs e)
    {
        if (sender is FrameworkElement fe && fe.ContextMenu != null)
        {
            fe.ContextMenu.PlacementTarget = fe;
            fe.ContextMenu.Placement = System.Windows.Controls.Primitives.PlacementMode.Top;
            fe.ContextMenu.IsOpen = true;
            e.Handled = true;
        }
    }

    private void Messages_CollectionChanged(object? sender, System.Collections.Specialized.NotifyCollectionChangedEventArgs e)
    {
        if (e.Action == System.Collections.Specialized.NotifyCollectionChangedAction.Add)
        {
            var sv = FindName("MessageScroll") as ScrollViewer;
            sv?.ScrollToBottom();
        }
    }

    /// <summary>
    /// 消息气泡内的 FlowDocumentScrollViewer / TextBox 会在 MouseWheel 冒泡阶段吞掉滚轮
    /// （即使 VerticalScrollBarVisibility=Disabled），导致外层 MessageScroll 收不到滚轮、
    /// 鼠标停在回答正文上无法上下滚动。在 Preview 隧道阶段由外层统一消费并滚动消息列表。
    /// </summary>
    private void MessageScroll_PreviewMouseWheel(object sender, MouseWheelEventArgs e)
    {
        if (sender is not ScrollViewer sv)
        {
            return;
        }

        // 先放行给事件源所在的内层可滚动控件（WebView2 气泡、思考详情等）：
        // 内层还能沿滚轮方向继续滚时绝不接管，否则其内部滚动全部失效；
        // 内层已到边界（或无内层滚动区）才由外层统一滚动。
        if (e.OriginalSource is DependencyObject source && CanInnerScroll(source, e.Delta))
        {
            return; // 不置 Handled，让事件正常到达内层
        }

        // 一格滚轮（±120）约等于 3 行，按 24px 行高折算，与系统默认手感接近
        double pixels = e.Delta / 120.0 * 48.0;
        double maxOffset = Math.Max(0, sv.ExtentHeight - sv.ViewportHeight);
        double newOffset = Math.Max(0, Math.Min(maxOffset, sv.VerticalOffset - pixels));
        sv.ScrollToVerticalOffset(newOffset);
        e.Handled = true;
    }

    /// <summary>判断滚轮事件源到外层之间是否存在可沿本次方向继续滚动的内层滚动区。</summary>
    private static bool CanInnerScroll(DependencyObject source, double delta)
    {
        DependencyObject? current = source;
        while (current is not null)
        {
            if (current is ScrollViewer inner && inner.ScrollableHeight > 0)
            {
                return delta < 0
                    ? inner.VerticalOffset < inner.ScrollableHeight // 向下滚（内容上移）
                    : inner.VerticalOffset > 0;                     // 向上滚
            }
            // 事件源可能是 Run/TextElement 等 ContentElement（非 Visual），
            // 直接调 VisualTreeHelper.GetParent 会抛 InvalidOperationException；
            // 非 Visual 节点用逻辑树向上，仍找不到再终止。
            current = current is System.Windows.Media.Visual or System.Windows.Media.Media3D.Visual3D
                ? System.Windows.Media.VisualTreeHelper.GetParent(current)
                : System.Windows.LogicalTreeHelper.GetParent(current);
        }
        return false;
    }

    /// <summary>处理内容增加时的自动滚动，保持在底部时的吸附效果（用户向上回看时不强行拉回底部）。</summary>
    private void MessageScroll_ScrollChanged(object sender, ScrollChangedEventArgs e)
    {
        if (e.ExtentHeightChange > 0)
        {
            var oldScrollableHeight = e.ExtentHeight - e.ExtentHeightChange - e.ViewportHeight;
            if (oldScrollableHeight < 0) oldScrollableHeight = 0;
            
            // 如果在高度变化前滚动条靠近底部（差值<30），则自动滚动到底部跟随最新 token
            if (e.VerticalOffset >= oldScrollableHeight - 30)
            {
                var sv = sender as ScrollViewer;
                sv?.ScrollToBottom();
            }
        }
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        DebugLog.Debug("ChatView 已加载", "Chat");
        FocusInput();
    }

    // ── FlowDocumentScrollViewer detach/reattach ─────────────────────────
    // 每条消息的 DataTemplate 内有一个 FlowDocumentScrollViewer 绑定 RenderedDocument。
    // WPF 模板重建时同一 FlowDocument 实例可能同时属于两个 Viewer，
    // 抛出 "文档已属于另一个 FlowDocumentScrollViewer"。
    // 解决：Unloaded 时清空 Document 释放父引用，Loaded 时重新触发绑定。
    private void FlowDocViewer_Loaded(object sender, RoutedEventArgs e)
    {
        if (sender is FlowDocumentScrollViewer viewer)
        {
            var expr = System.Windows.Data.BindingOperations.GetBindingExpression(viewer, FlowDocumentScrollViewer.DocumentProperty);
            expr?.UpdateTarget();
        }
    }

    private void FlowDocViewer_Unloaded(object sender, RoutedEventArgs e)
    {
        if (sender is FlowDocumentScrollViewer viewer)
        {
            viewer.Document = null;
        }
    }

    /// <summary>HTML 体验气泡加载完成：接线引用角标桥（气泡内 a.cite 点击 → 复用 SourceRef 打开链路）。</summary>
    private void HtmlBubble_Loaded(object sender, RoutedEventArgs e)
    {
        if (sender is not Controls.HtmlAnswerBubble bubble)
        {
            return;
        }
        if (bubble.DataContext is not ViewModels.ChatMessage msg || _vm is null)
        {
            return;
        }
        bubble.CiteRequested -= OnHtmlBubbleCiteRequested;
        bubble.CiteRequested += OnHtmlBubbleCiteRequested;
    }

    private void OnHtmlBubbleCiteRequested(object? sender, int index)
    {
        if (sender is not Controls.HtmlAnswerBubble bubble)
        {
            return;
        }
        if (bubble.DataContext is not ViewModels.ChatMessage msg)
        {
            return;
        }
        // 复用现有链路：展开来源列表 + 高亮 + 打开抽屉
        msg.NotifySourceMarker(index);
        var src = msg.Sources?.FirstOrDefault(s => s.Index == index);
        if (src is not null)
        {
            _vm?.OpenSourceCommand.Execute(src);
        }
    }

    /// <summary>回车发送消息（Shift+Enter / Ctrl+Enter 换行）。使用 PreviewKeyDown 避免中文输入法(IME)的回车误触。</summary>
    private void ChatInputBox_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            // Shift+Enter 或 Ctrl+Enter 插入换行符，交给 TextBox 处理
            if (Keyboard.Modifiers.HasFlag(ModifierKeys.Shift) || Keyboard.Modifiers.HasFlag(ModifierKeys.Control))
            {
                return;
            }

            // 防抖
            if (e.IsRepeat) return;

            if (DataContext is ChatViewModel vm && vm.SendCommand.CanExecute(null))
            {
                vm.SendCommand.Execute(null);
            }
            else
            {
                DebugLog.Debug($"回车发送被忽略（IsBusy={DataContext is ChatViewModel v && v.IsBusy}，输入为空或生成中）", "Chat");
            }
            e.Handled = true;
        }
    }

    /// <summary>拖拽文件到输入框释放时，自动添加到待发送附件。</summary>
    private void ChatInputBox_Drop(object sender, DragEventArgs e)
    {
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            if (e.Data.GetData(DataFormats.FileDrop) is string[] files && files.Length > 0)
            {
                _vm?.AddAttachmentPaths(files);
                e.Handled = true;
            }
        }
    }

    /// <summary>拖拽文件悬停时允许复制。</summary>
    private void ChatInputBox_PreviewDragOver(object sender, DragEventArgs e)
    {
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            e.Effects = DragDropEffects.Copy;
            e.Handled = true;
        }
    }

    /// <summary>激活时自动聚焦输入框。</summary>
    private void FocusInput()
    {
        var box = FindName("ChatInputBox") as TextBox ?? GetFirstChild<TextBox>();
        box?.Focus();
    }

    private static T? GetFirstChild<T>(DependencyObject? parent = null) where T : DependencyObject
    {
        if (parent is null) return null;
        if (parent is T found) return found;
        var count = VisualTreeHelper.GetChildrenCount(parent);
        for (int i = 0; i < count; i++)
        {
            var result = GetFirstChild<T>(VisualTreeHelper.GetChild(parent, i));
            if (result != null) return result;
        }
        return null;
    }

    /// <summary>折叠/展开消息的「思考过程」区（按钮 DataContext 即消息实例）。</summary>
    private void ToggleThinking_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.DataContext is ChatMessage msg)
        {
            msg.IsThinkingExpanded = !msg.IsThinkingExpanded;
        }
    }

    /// <summary>抽屉内 URL 点击：在浏览器中打开（仅 http/https，防危险协议）。</summary>
    private void OpenWebSource_Click(object sender, MouseButtonEventArgs e)
    {
        if (_vm?.SelectedSource?.Url is not { Length: > 0 } url)
        {
            return;
        }
        if (Uri.TryCreate(url, UriKind.Absolute, out var uri)
            && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps))
        {
            System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo(uri.AbsoluteUri)
                {
                    UseShellExecute = true,
                });
        }
    }

    /// <summary>点击自定义管理面板的半透明遮罩时关闭面板。</summary>
    private void CustomManagerOverlay_Click(object sender, MouseButtonEventArgs e)
    {
        if (e.OriginalSource is Border) // 只响应遮罩层本身的点击，不拦截子元素
        {
            _vm?.CloseCustomManagerCommand.Execute(null);
        }
    }
}