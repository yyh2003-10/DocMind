namespace DocMind.Helpers;

/// <summary>
/// WPF ValueConverter: true → collapsed sidebar width (48), false → expanded (160).
/// </summary>
public sealed class BooleanToSidebarWidthConverter : System.Windows.Data.IValueConverter
{
    public object Convert(object? value, System.Type targetType, object? parameter, System.Globalization.CultureInfo culture)
    {
        return value is true
            ? new System.Windows.GridLength(48)
            : new System.Windows.GridLength(160);
    }

    public object ConvertBack(object? value, System.Type targetType, object? parameter, System.Globalization.CultureInfo culture)
        => throw new System.NotSupportedException();
}
