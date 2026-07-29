using System.ComponentModel;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using DocMind.ViewModels;

namespace DocMind.Views;

public partial class SearchView : UserControl
{
    private SearchViewModel? _vm;

    public SearchView()
    {
        InitializeComponent();
        Loaded += (_, _) => FocusFirstInput();
        DataContextChanged += OnSearchViewDataContextChanged;
    }

    private void OnSearchViewDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        // 取消订阅旧的 VM
        if (e.OldValue is SearchViewModel oldVm)
            oldVm.PropertyChanged -= OnVmPropertyChanged;

        // 订阅新的 VM
        if (e.NewValue is SearchViewModel newVm)
            newVm.PropertyChanged += OnVmPropertyChanged;

        _vm = e.NewValue as SearchViewModel;
    }

    private void OnVmPropertyChanged(object? sender, PropertyChangedEventArgs e)
    {
        // 当选中的搜索结果变化时，刷新详情区高亮
        if (e.PropertyName == nameof(SearchViewModel.SelectedHit) ||
            e.PropertyName == nameof(SearchViewModel.Query))
        {
            Dispatcher.BeginInvoke(() => UpdateDetailHighlight());
        }
    }

    /// <summary>更新详情面板中的关键词高亮。</summary>
    private void UpdateDetailHighlight()
    {
        if (_vm is null) return;
        var query = _vm.Query?.Trim();
        if (string.IsNullOrEmpty(query))
            return;

        // 查找详情面板中的内容 TextBlock（通过名称或树遍历）
        var detailText = DetailContentText;
        if (detailText is null) return;

        var text = detailText.Text;
        if (string.IsNullOrEmpty(text))
            return;

        ApplyHighlight(detailText, text, query);
    }

    /// <summary>在 TextBlock 中高亮搜索词匹配。</summary>
    private static void ApplyHighlight(TextBlock tb, string text, string query)
    {
        var words = query.Split([' ', '\t', '　'], StringSplitOptions.RemoveEmptyEntries)
                         .Distinct()
                         .Select(Regex.Escape)
                         .ToList();
        if (words.Count == 0) return;

        var pattern = string.Join("|", words);
        try
        {
            var matches = Regex.Matches(text, pattern, RegexOptions.IgnoreCase);
            if (matches.Count == 0) return;

            tb.Inlines.Clear();
            int lastPos = 0;

            foreach (Match match in matches)
            {
                if (match.Index > lastPos)
                    tb.Inlines.Add(new Run(text[lastPos..match.Index]));

                tb.Inlines.Add(new Run(text[match.Index..(match.Index + match.Length)])
                {
                    Background = new SolidColorBrush(Color.FromRgb(255, 243, 205)),
                });

                lastPos = match.Index + match.Length;
            }

            if (lastPos < text.Length)
                tb.Inlines.Add(new Run(text[lastPos..]));
        }
        catch (RegexParseException)
        {
            // 正则异常时回退到纯文本
        }
    }

    /// <summary>激活时自动聚焦搜索框。</summary>
    private void FocusFirstInput()
    {
        var searchBox = this.FindName("QueryBox") as TextBox
                        ?? GetFirstChild<TextBox>();
        searchBox?.Focus();
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

    /// <summary>回车触发搜索。</summary>
    private void QueryBox_KeyUp(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && _vm is not null)
        {
            _vm.SearchCommand.Execute(null);
        }
    }
}
