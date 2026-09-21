using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
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
        SetDropZoneHighlight(false);
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

    private void DropZone_DragEnter(object sender, DragEventArgs e)
    {
        SetDropZoneHighlight(true);
        View_DragOver(sender, e);
    }

    private void DropZone_DragLeave(object sender, DragEventArgs e)
    {
        SetDropZoneHighlight(false);
    }

    /// <summary>拖入时用主色淡底 + 主色描边，离开恢复。</summary>
    private void SetDropZoneHighlight(bool active)
    {
        if (DropZone is null)
        {
            return;
        }

        if (active)
        {
            DropZone.SetResourceReference(Border.BackgroundProperty, "PrimaryLightBrush");
            DropZone.SetResourceReference(Border.BorderBrushProperty, "PrimaryBrush");
        }
        else
        {
            DropZone.SetResourceReference(Border.BackgroundProperty, "SurfaceBrush");
            DropZone.SetResourceReference(Border.BorderBrushProperty, "BorderBrush");
        }
    }

    /// <summary>新建分组名称输入框：回车确认、Esc 取消。</summary>
    private void NewCollectionName_KeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (DataContext is not ImportViewModel vm)
        {
            return;
        }
        if (e.Key == System.Windows.Input.Key.Enter)
        {
            if (vm.ConfirmCreateCollectionCommand.CanExecute(null))
            {
                vm.ConfirmCreateCollectionCommand.Execute(null);
            }
            e.Handled = true;
        }
        else if (e.Key == System.Windows.Input.Key.Escape)
        {
            vm.CancelCreateCollectionCommand.Execute(null);
            e.Handled = true;
        }
    }
}
