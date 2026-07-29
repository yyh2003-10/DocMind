using System.Windows.Controls;
using System.Windows.Input;
using DocMind.ViewModels;

namespace DocMind.Views;

public partial class SearchView : UserControl
{
    public SearchView()
    {
        InitializeComponent();
    }

    /// <summary>回车触发搜索。</summary>
    private void QueryBox_KeyUp(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && DataContext is SearchViewModel vm)
        {
            vm.SearchCommand.Execute(null);
        }
    }
}
