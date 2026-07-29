using System.Windows;
using System.Windows.Controls;
using DocMind.ViewModels;

namespace DocMind.Views;

public partial class ConvertView : UserControl
{
    public ConvertView()
    {
        InitializeComponent();
        AllowDrop = true;
        Drop += View_Drop;
        DragOver += View_DragOver;
    }

    /// <summary>拖放源文件到视图：写入 InputPath。</summary>
    private void View_Drop(object sender, DragEventArgs e)
    {
        if (DataContext is not ConvertViewModel vm)
        {
            return;
        }
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            var paths = (string[])e.Data.GetData(DataFormats.FileDrop);
            if (paths is { Length: > 0 })
            {
                vm.InputPath = paths[0];
                e.Handled = true;
            }
        }
    }

    /// <summary>拖拽悬停时强制效果显示。</summary>
    private void View_DragOver(object sender, DragEventArgs e)
    {
        e.Effects = e.Data.GetDataPresent(DataFormats.FileDrop)
            ? DragDropEffects.Copy
            : DragDropEffects.None;
        e.Handled = true;
    }
}
