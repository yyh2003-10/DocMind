using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;

namespace DocMind.Helpers;

/// <summary>
/// 将（HighlightedSourceIndex, Source.Index）映射为卡片背景色：
/// 点击正文 [n] 后对应来源卡片短暂高亮，帮助用户对齐引用。
/// 高亮色跟随主题（深色下用主色淡染，避免浅蓝块刺眼）。
/// </summary>
public sealed class SourceHighlightConverter : IMultiValueConverter
{
    private static readonly Brush TransparentBrush = Brushes.Transparent;

    private static Brush ResolveHighlightBrush()
    {
        try
        {
            if (Application.Current?.TryFindResource("PrimaryLightBrush") is Brush themeBrush)
                return themeBrush;
            if (Application.Current?.TryFindResource("SelectedBrush") is Brush selected)
                return selected;
        }
        catch
        {
            // 资源不可用时落回中性淡染
        }
        return new SolidColorBrush(Color.FromArgb(0x66, 0x81, 0x8C, 0xF8));
    }

    public object Convert(object[] values, Type targetType, object parameter, CultureInfo culture)
    {
        if (values.Length < 2)
        {
            return TransparentBrush;
        }
        int? highlighted = values[0] switch
        {
            int i => i,
            null => null,
            _ => null,
        };
        if (highlighted is null || values[1] is not int index)
        {
            return TransparentBrush;
        }
        return highlighted.Value == index ? ResolveHighlightBrush() : TransparentBrush;
    }

    public object[] ConvertBack(object value, Type[] targetTypes, object parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
