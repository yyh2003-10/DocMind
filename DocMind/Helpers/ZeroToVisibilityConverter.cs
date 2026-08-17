using System.Windows.Data;
using System.Windows;
using System.Globalization;

namespace DocMind.Helpers
{
    public class ZeroToVisibilityConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
        {
            return value switch
            {
                int i => i > 0 ? Visibility.Visible : Visibility.Collapsed,
                long l => l > 0 ? Visibility.Visible : Visibility.Collapsed,
                _ => Visibility.Collapsed
            };
        }

        public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture)
            => throw new NotSupportedException();
    }
}