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

    private void Messages_CollectionChanged(object? sender, System.Collections.Specialized.NotifyCollectionChangedEventArgs e)
    {
        if (e.Action == System.Collections.Specialized.NotifyCollectionChangedAction.Add)
        {
            var sv = FindName("MessageScroll") as ScrollViewer;
            sv?.ScrollToBottom();
        }
    }

    /// <summary>处理内容增加时的自动滚动，保持在底部时的吸附效果。</summary>
    private void MessageScroll_ScrollChanged(object sender, ScrollChangedEventArgs e)
    {
        if (e.ExtentHeightChange > 0)
        {
            var oldScrollableHeight = e.ExtentHeight - e.ExtentHeightChange - e.ViewportHeight;
            if (oldScrollableHeight < 0) oldScrollableHeight = 0;
            
            // 如果在高度变化前滚动条靠近底部（差值<10），则自动滚动到底部
            if (e.VerticalOffset >= oldScrollableHeight - 10)
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

    /// <summary>回车发送消息（Shift+Enter 换行）。使用 PreviewKeyDown 避免中文输入法(IME)的回车误触。</summary>
    private void ChatInputBox_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && Keyboard.Modifiers != ModifierKeys.Shift)
        {
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
}