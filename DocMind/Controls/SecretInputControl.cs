using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;

namespace DocMind.Controls;

/// <summary>
/// 密钥输入控件：默认掩码 + 眼睛显隐切换。
/// 兼容既有 TextBox 绑定链路（Text 双向绑定原样透传），不引入 PasswordBox。
/// 掩码仅是呈现层（TemplateBinding MaskedText），Text 属性始终为真实密钥值。
/// 图标缺失时退化为明文 TextBox（同现状），不影响保存。
/// </summary>
public sealed class SecretInputControl : TextBox
{
    // ─────────────────────────  IsRevealed  ─────────────────────────

    /// <summary>明文可见（false=默认掩码）。点击眼睛按钮切换，清空后自动回归掩码。</summary>
    public bool IsRevealed
    {
        get => (bool)GetValue(IsRevealedProperty);
        set => SetValue(IsRevealedProperty, value);
    }

    public static readonly DependencyProperty IsRevealedProperty =
        DependencyProperty.Register(
            nameof(IsRevealed),
            typeof(bool),
            typeof(SecretInputControl),
            new PropertyMetadata(false, OnIsRevealedChanged));

    // ─────────────────────────  MaskedText（只读）  ─────────────────────────

    /// <summary>掩码呈现串（只读，模板 TextBlock 绑定用）。如 "••••••••"。</summary>
    public string MaskedText
    {
        get => (string)GetValue(MaskedTextProperty);
        private set => SetValue(MaskedTextPropertyKey, value);
    }

    private static readonly DependencyPropertyKey MaskedTextPropertyKey =
        DependencyProperty.RegisterReadOnly(
            nameof(MaskedText),
            typeof(string),
            typeof(SecretInputControl),
            new PropertyMetadata(string.Empty));

    public static readonly DependencyProperty MaskedTextProperty =
        MaskedTextPropertyKey.DependencyProperty;

    // ─────────────────────────  常量与静态构造  ─────────────────────────

    private const char MaskChar = '\u2022'; // • U+2022

    static SecretInputControl()
    {
        DefaultStyleKeyProperty.OverrideMetadata(
            typeof(SecretInputControl),
            new FrameworkPropertyMetadata(typeof(SecretInputControl)));
    }

    // ─────────────────────────  实例逻辑  ─────────────────────────

    public SecretInputControl()
    {
        IsReadOnly = true; // 默认掩码态只读，明文切换时解除
        UpdateMaskedText();
    }

    private static void OnIsRevealedChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        var ctrl = (SecretInputControl)d;
        var revealed = (bool)e.NewValue;
        // 掩码时只读（防误输入），明文时可编辑
        ctrl.IsReadOnly = !revealed;
    }

    protected override void OnTextChanged(TextChangedEventArgs e)
    {
        base.OnTextChanged(e);
        UpdateMaskedText();
        // 清空后自动回归掩码态
        if (string.IsNullOrEmpty(Text))
        {
            IsRevealed = false;
        }
    }

    public override void OnApplyTemplate()
    {
        base.OnApplyTemplate();

        // 挂接眼睛按钮模板契约
        if (GetTemplateChild("PART_EyeButton") is ButtonBase eyeButton)
        {
            eyeButton.Click -= OnEyeButtonClick;
            eyeButton.Click += OnEyeButtonClick;
        }
    }

    private void OnEyeButtonClick(object sender, RoutedEventArgs e)
    {
        IsRevealed = !IsRevealed;
        // 切换为明文后聚焦，方便直接编辑
        if (IsRevealed)
        {
            Focus();
            CaretIndex = Text.Length;
        }
    }

    private void UpdateMaskedText()
    {
        MaskedText = string.IsNullOrEmpty(Text)
            ? string.Empty
            : new string(MaskChar, Text.Length);
    }
}