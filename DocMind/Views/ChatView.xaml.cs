using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Views;

public partial class ChatView : UserControl
{
    public ChatView()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        DataContextChanged += (_, _) => { };
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        DebugLog.Debug("ChatView 已加载", "Chat");
        FocusInput();
    }

    /// <summary>回车发送消息（Shift+Enter 换行）。</summary>
    private void ChatInputBox_KeyUp(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && Keyboard.Modifiers != ModifierKeys.Shift)
        {
            if (DataContext is ChatViewModel vm && vm.SendCommand.CanExecute(null))
            {
                vm.SendCommand.Execute(null);
            }
            else
            {
                // 排查"回车没反应"：生成中或输入为空时回车被忽略
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