using System.Windows.Data;
using System.Windows;
using System.Globalization;

namespace DocMind.Helpers
{
    public class NullToVisibilityConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
        {
            if (value is null)
                return Visibility.Collapsed;

            if (value is string s)
                return string.IsNullOrWhiteSpace(s) ? Visibility.Collapsed : Visibility.Visible;

            return Visibility.Visible;
        }

        public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture)
            => throw new NotSupportedException();
    }
}