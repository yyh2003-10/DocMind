using System.Windows.Data;
using System.Windows;
namespace DocMind.Helpers
{
    public class BoolToVisibilityInvertConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object? parameter, System.Globalization.CultureInfo culture)
            => value is bool b && !b ? Visibility.Visible : Visibility.Collapsed;

        public object ConvertBack(object? value, Type targetType, object? parameter, System.Globalization.CultureInfo culture)
            => throw new NotSupportedException();
    }
}