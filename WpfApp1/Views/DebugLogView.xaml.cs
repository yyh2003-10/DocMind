using System.Collections.Specialized;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using DocMind.ViewModels;

namespace DocMind.Views
{
    public partial class DebugLogView : UserControl
    {
        public DebugLogView()
        {
            InitializeComponent();
            Loaded += OnLoaded;
            Unloaded += OnUnloaded;
        }

        private void OnLoaded(object sender, System.Windows.RoutedEventArgs e)
        {
            if (DataContext is DebugLogViewModel vm)
            {
                vm.Lines.CollectionChanged += OnLinesChanged;
                ScrollToEnd();
            }
        }

        private void OnUnloaded(object sender, System.Windows.RoutedEventArgs e)
        {
            if (DataContext is DebugLogViewModel vm)
            {
                vm.Lines.CollectionChanged -= OnLinesChanged;
            }
        }

        private void OnLinesChanged(object? sender, NotifyCollectionChangedEventArgs e)
        {
            ScrollToEnd();
        }

        private void ScrollToEnd()
        {
            if (LogList.Items.Count == 0)
                return;

            // 用 ScrollViewer 直接滚动到底部，避免 VirtualizingStackPanel 的容器预取竞态
            if (FindVisualChild<ScrollViewer>(LogList) is { } sv)
            {
                sv.ScrollToBottom();
            }
            else
            {
                // fallback：ListBox 自身无 ScrollViewer 时用 ScrollIntoView
                try { LogList.ScrollIntoView(LogList.Items[^1]); }
                catch (ArgumentOutOfRangeException) { /* 忽略虚拟化竞态 */ }
            }
        }

        /// <summary>在可视树中查找第一个指定类型的子元素。</summary>
        private static T? FindVisualChild<T>(DependencyObject parent) where T : DependencyObject
        {
            for (int i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
            {
                var child = VisualTreeHelper.GetChild(parent, i);
                if (child is T match)
                    return match;
                if (FindVisualChild<T>(child) is { } nested)
                    return nested;
            }
            return null;
        }
    }
}
