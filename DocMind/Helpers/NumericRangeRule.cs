using System.Globalization;
using System.Windows.Controls;

namespace DocMind.Helpers;

/// <summary>
/// 数值范围校验（WPF Binding ValidationRule）。
/// 用法：
///   &lt;TextBox.Text&gt;
///     &lt;Binding Path="LlmTemperature" UpdateSourceTrigger="PropertyChanged"&gt;
///       &lt;Binding.ValidationRules&gt;
///         &lt;helpers:NumericRangeRule Min="0" Max="1.5" FieldName="对话温度"/&gt;
///       &lt;/Binding.ValidationRules&gt;
///     &lt;/Binding&gt;
///   &lt;/TextBox.Text&gt;
/// 非数字或越界时：红框 + ToolTip 提示；来源值不更新（保存前统一再校验一遍）。
/// </summary>
public class NumericRangeRule : ValidationRule
{
    public double Min { get; set; }
    public double Max { get; set; }
    /// <summary>错误提示里显示的字段名（如「对话温度」）。</summary>
    public string FieldName { get; set; } = "数值";

    public override ValidationResult Validate(object value, CultureInfo cultureInfo)
    {
        var text = value?.ToString()?.Trim();

        if (string.IsNullOrEmpty(text))
        {
            return new ValidationResult(false, $"{FieldName}不能为空");
        }

        // 兼容中文输入法的全角数字/负号/小数点
        text = text
            .Replace('０', '0').Replace('１', '1').Replace('２', '2').Replace('３', '3').Replace('４', '4')
            .Replace('５', '5').Replace('６', '6').Replace('７', '7').Replace('８', '8').Replace('９', '9')
            .Replace('。', '.').Replace('．', '.').Replace('－', '-');

        if (!double.TryParse(text, NumberStyles.Float, CultureInfo.CurrentCulture, out var n)
            && !double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out n))
        {
            return new ValidationResult(false, $"{FieldName}必须是数字");
        }

        if (n < Min || n > Max)
        {
            var minText = Min == Math.Floor(Min) ? Min.ToString("0") : Min.ToString("0.##");
            var maxText = Max == Math.Floor(Max) ? Max.ToString("0") : Max.ToString("0.##");
            return new ValidationResult(false, $"{FieldName}范围为 {minText} ~ {maxText}");
        }

        return ValidationResult.ValidResult;
    }
}
