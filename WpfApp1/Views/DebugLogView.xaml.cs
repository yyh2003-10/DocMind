using System.Collections.Specialized;
using System.Windows.Controls;
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
            {
                return;
            }
            LogList.ScrollIntoView(LogList.Items[^1]);
        }
    }
}
