using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;

namespace DocMind.Helpers;

/// <summary>日志行文本 → 前景色：ERROR/FATAL 红、WARN 橙、其余默认。
/// 颜色从当前主题资源动态取（跟随浅色/深色主题切换）。</summary>
public sealed class LogLevelToBrushConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        var line = value as string;
        if (string.IsNullOrEmpty(line))
        {
            return GetBrush("BodyBrush", Brushes.Gray);
        }

        if (line.Contains("[ERROR]", StringComparison.Ordinal) || line.Contains("[FATAL]", StringComparison.Ordinal))
        {
            return GetBrush("DangerBrush", Brushes.IndianRed);
        }
        if (line.Contains("[WARN", StringComparison.Ordinal))
        {
            return GetBrush("WarningBrush", Brushes.Goldenrod);
        }
        return GetBrush("BodyBrush", Brushes.Gray);
    }

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture)
        => throw new NotSupportedException();

    private static Brush GetBrush(string key, Brush fallback)
        => Application.Current?.TryFindResource(key) is Brush b ? b : fallback;
}
