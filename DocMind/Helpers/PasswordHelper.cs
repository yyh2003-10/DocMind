using System.Windows;
using System.Windows.Controls;

namespace DocMind.Helpers;

/// <summary>
/// PasswordBox 的可绑定密码（PasswordBox.Password 出于安全不参与双向 Binding）。
/// 用法：
///   <PasswordBox helpers:PasswordHelper.Attach="True"
///                helpers:PasswordHelper.BoundPassword="{Binding LlmApiKey, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"/>
/// 实现要点：真实密码只存于 VM；helper 内部比对防回环（避免输入一位 → 写 VM → VM 回写 → 光标归位）。
/// </summary>
public static class PasswordHelper
{
    public static readonly DependencyProperty AttachProperty = DependencyProperty.RegisterAttached(
        "Attach", typeof(bool), typeof(PasswordHelper),
        new FrameworkPropertyMetadata(false, OnAttachChanged));

    public static bool GetAttach(DependencyObject obj) => (bool)obj.GetValue(AttachProperty);
    public static void SetAttach(DependencyObject obj, bool value) => obj.SetValue(AttachProperty, value);

    /// <summary>双向绑定的密码值。绑定写入时不触发回写 VM（防回环）。</summary>
    public static readonly DependencyProperty BoundPasswordProperty = DependencyProperty.RegisterAttached(
        "BoundPassword", typeof(string), typeof(PasswordHelper),
        new FrameworkPropertyMetadata(string.Empty, FrameworkPropertyMetadataOptions.BindsTwoWayByDefault, OnBoundPasswordChanged));

    public static string GetBoundPassword(DependencyObject obj) => (string)obj.GetValue(BoundPasswordProperty);
    public static void SetBoundPassword(DependencyObject obj, string value) => obj.SetValue(BoundPasswordProperty, value);

    /// <summary>helper 正在内部写 PasswordBox.Password 时为 true，抑制 PasswordChanged 回环。</summary>
    private static readonly DependencyProperty IsUpdatingProperty = DependencyProperty.RegisterAttached(
        "IsUpdating", typeof(bool), typeof(PasswordHelper), new PropertyMetadata(false));

    private static bool GetIsUpdating(DependencyObject obj) => (bool)obj.GetValue(IsUpdatingProperty);
    private static void SetIsUpdating(DependencyObject obj, bool value) => obj.SetValue(IsUpdatingProperty, value);

    private static void OnAttachChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is not PasswordBox pb) return;

        if ((bool)e.NewValue)
        {
            pb.PasswordChanged += OnPasswordChanged;
        }
        else
        {
            pb.PasswordChanged -= OnPasswordChanged;
        }
    }

    /// <summary>用户在 PasswordBox 中输入 → 同步到 BoundPassword（→ VM）。</summary>
    private static void OnPasswordChanged(object sender, RoutedEventArgs e)
    {
        if (sender is not PasswordBox pb) return;
        if (GetIsUpdating(pb)) return;

        SetIsUpdating(pb, true);
        try
        {
            SetBoundPassword(pb, pb.Password);
        }
        finally
        {
            SetIsUpdating(pb, false);
        }
    }

    /// <summary>VM/绑定写入 → 同步到 PasswordBox.Password（清空表单、Revert 时）。</summary>
    private static void OnBoundPasswordChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is not PasswordBox pb) return;
        if (GetIsUpdating(pb)) return;

        SetIsUpdating(pb, true);
        try
        {
            var newText = (string?)e.NewValue ?? string.Empty;
            if (pb.Password != newText)
            {
                pb.Password = newText;
            }
        }
        finally
        {
            SetIsUpdating(pb, false);
        }
    }
}
