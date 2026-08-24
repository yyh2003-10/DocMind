using System;
using System.Globalization;
using System.Windows.Data;

namespace DocMind.Helpers;

/// <summary>把思考/搜索步骤文案映射为链路阶段图标（附件/实体关系/检索/避坑/联网/生成）。</summary>
public sealed class ThinkingStepIconConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture)
    {
        var text = (value as string) ?? string.Empty;
        if (text.Contains("附件", StringComparison.OrdinalIgnoreCase))
        {
            return "📎";
        }
        if (text.Contains("实体关系", StringComparison.OrdinalIgnoreCase))
        {
            return "🔗";
        }
        if (text.Contains("避坑", StringComparison.OrdinalIgnoreCase))
        {
            return "🛡️";
        }
        if (text.Contains("联网", StringComparison.OrdinalIgnoreCase))
        {
            return "🌐";
        }
        if (text.Contains("检索", StringComparison.OrdinalIgnoreCase))
        {
            return "📚";
        }
        if (text.Contains("生成", StringComparison.OrdinalIgnoreCase))
        {
            return "✍️";
        }
        return "🧠";
    }

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
