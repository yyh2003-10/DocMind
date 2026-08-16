using System.Windows;
using System.Windows.Controls;
using DocMind.ViewModels;

namespace DocMind.Views;

public partial class ImportView : UserControl
{
    public ImportView()
    {
        InitializeComponent();
    }

    /// <summary>拖放落地：取第一个文件/目录路径写入 VM。</summary>
    private void View_Drop(object sender, DragEventArgs e)
    {
        if (DataContext is not ImportViewModel vm)
        {
            return;
        }
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            var paths = (string[])e.Data.GetData(DataFormats.FileDrop);
            if (paths is { Length: > 0 })
            {
                vm.SelectedPath = paths[0];
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
