namespace DocMind.Controls;

using System;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using DocMind.Models;
using DocMind.ViewModels;

/// <summary>
/// 原著查证容器：上方「关键点对照」（答案表述 + 库内引用原文），下方嵌入原文件
/// （PDF 高亮定位 / 文本全文降级），满足「原文件与关键点对照」而不是只看切片。
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
            TextViewerContainer.Visibility = Visibility.Collapsed;
            TextLocateBanner.Visibility = Visibility.Collapsed;
            return;
        }

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
            TextLocateBanner.Visibility = Visibility.Collapsed;

            InnerPdfViewer.SourcePath = src.Source;
            InnerPdfViewer.TargetPage = src.Page;
            InnerPdfViewer.TargetSnippet = src.Snippet;
        }
        else
        {
            PdfViewerContainer.Visibility = Visibility.Collapsed;
            TextViewerContainer.Visibility = Visibility.Visible;
            UpdateTextOriginalBody(src);
        }
    }

    /// <summary>
    /// 非 PDF 降级：优先展示原文件全文（便于对照），找不到文件时展示引用切片并明确提示。
    /// </summary>
    private void UpdateTextOriginalBody(SourceRef src)
    {
        if (src.IsWebSource)
        {
            TextLocateBanner.Visibility = Visibility.Collapsed;
            OriginalFileBodyText.Visibility = Visibility.Collapsed;
            SnippetFallbackBox.Visibility = Visibility.Visible;
            return;
        }

        var path = src.Source;
        var snippet = (src.Snippet ?? string.Empty).Trim();

        if (!string.IsNullOrWhiteSpace(path) && File.Exists(path))
        {
            try
            {
                var text = File.ReadAllText(path);
                var needle = snippet.Length > 80 ? snippet[..80] : snippet;
                bool located = needle.Length > 0
                    && text.Contains(needle, StringComparison.OrdinalIgnoreCase);

                TextLocateBanner.Visibility = Visibility.Visible;
                if (located)
                {
                    TextLocateBanner.Background = (Brush)FindResource("PrimaryLightBrush");
                    TextLocateBannerText.Foreground = (Brush)FindResource("PrimaryBrush");
                    TextLocateBannerText.Text = "已定位到原文件匹配段，请对照上方关键点与下方原文。";
                }
                else
                {
                    TextLocateBanner.Background = (Brush)FindResource("WarningLightBrush");
                    TextLocateBannerText.Foreground = (Brush)FindResource("WarningBrush");
                    TextLocateBannerText.Text = "未能自动定位到精确段落，下方为原文件全文，请人工对照关键点。";
                }

                var display = text.Length > 8000 ? text[..8000] + "\n\n…（原文件过长，已截断；可点「打开文件」查看完整内容）" : text;
                OriginalFileBodyText.Text = display;
                OriginalFileBodyText.Visibility = Visibility.Visible;
                SnippetFallbackBox.Visibility = Visibility.Collapsed;
                return;
            }
            catch
            {
                // 读取失败走切片降级
            }
        }

        TextLocateBanner.Visibility = Visibility.Visible;
        TextLocateBanner.Background = (Brush)FindResource("WarningLightBrush");
        TextLocateBannerText.Foreground = (Brush)FindResource("WarningBrush");
        TextLocateBannerText.Text = string.IsNullOrWhiteSpace(path)
            ? "该来源未提供本地文件路径，仅展示库内引用原文。"
            : "原文件不在本地磁盘（可能已被移动），仅展示库内引用原文。";

        OriginalFileBodyText.Visibility = Visibility.Collapsed;
        SnippetFallbackBox.Visibility = Visibility.Visible;
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
                ToggleExpandButton.ToolTip = "还原协同抽屉宽度";
            }
            else
            {
                vm.SourceDrawerWidth = Math.Max(380, _previousWidth);
                _isExpanded = false;
                ToggleExpandButton.ToolTip = "切换半屏沉浸式对照阅读";
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
