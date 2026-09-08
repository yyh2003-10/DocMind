namespace DocMind.Controls;

using System;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using DocMind.Models;
using DocMind.ViewModels;

/// <summary>
/// 统一原著查证承载容器：根据当前切片来源自动切换 PDF 矢量阅览高亮器或富文本降级视图。
/// </summary>
public partial class GroundTruthViewerControl : UserControl
{
    public static readonly DependencyProperty SelectedSourceProperty =
        DependencyProperty.Register(
            nameof(SelectedSource),
            typeof(SourceRef),
            typeof(GroundTruthViewerControl),
            new PropertyMetadata(null, OnSelectedSourceChanged));

    public SourceRef? SelectedSource
    {
        get => (SourceRef?)GetValue(SelectedSourceProperty);
        set => SetValue(SelectedSourceProperty, value);
    }

    private double _previousWidth = 460;
    private bool _isExpanded;

    public GroundTruthViewerControl()
    {
        InitializeComponent();
    }

    private static void OnSelectedSourceChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is GroundTruthViewerControl control)
        {
            control.UpdateViewerMode();
        }
    }

    private void UpdateViewerMode()
    {
        var src = SelectedSource;
        if (src == null)
        {
            PdfViewerContainer.Visibility = Visibility.Collapsed;
            TextViewerContainer.Visibility = Visibility.Visible;
            return;
        }

        // 判断是否为可渲染的本地 PDF 文档
        bool isLocalPdf = !src.IsWebSource
            && !string.IsNullOrWhiteSpace(src.Source)
            && !src.Source.StartsWith("note:", StringComparison.OrdinalIgnoreCase)
            && (string.Equals(src.Format, "pdf", StringComparison.OrdinalIgnoreCase)
                || src.Source.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase))
            && File.Exists(src.Source);

        if (isLocalPdf)
        {
            TextViewerContainer.Visibility = Visibility.Collapsed;
            PdfViewerContainer.Visibility = Visibility.Visible;

            InnerPdfViewer.SourcePath = src.Source;
            InnerPdfViewer.TargetPage = src.Page;
            InnerPdfViewer.TargetSnippet = src.Snippet;
        }
        else
        {
            PdfViewerContainer.Visibility = Visibility.Collapsed;
            TextViewerContainer.Visibility = Visibility.Visible;
        }
    }

    private void ToggleExpandButton_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is ChatViewModel vm)
        {
            if (!_isExpanded)
            {
                _previousWidth = vm.SourceDrawerWidth;
                vm.SourceDrawerWidth = 880;
                _isExpanded = true;
                ToggleExpandButton.Content = "❐";
                ToggleExpandButton.ToolTip = "还原协同抽屉宽度";
            }
            else
            {
                vm.SourceDrawerWidth = Math.Max(380, _previousWidth);
                _isExpanded = false;
                ToggleExpandButton.Content = "⛶";
                ToggleExpandButton.ToolTip = "切换半屏沉浸式阅读";
            }
        }
    }

    private void OpenWebUrl_Click(object sender, MouseButtonEventArgs e)
    {
        if (SelectedSource?.Url is { Length: > 0 } url
            && (url.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
                || url.StartsWith("https://", StringComparison.OrdinalIgnoreCase)))
        {
            try
            {
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
            catch { }
        }
    }
}
