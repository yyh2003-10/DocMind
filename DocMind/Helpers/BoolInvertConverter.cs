using System.Globalization;
using System.Windows.Data;

namespace DocMind.Helpers;

/// <summary>布尔值取反转换器（用于 IsEnabled 绑定取反场景）。</summary>
public sealed class BoolInvertConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
        value is bool b ? !b : value;

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        value is bool b ? !b : value;
}