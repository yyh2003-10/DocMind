using System.Windows;
using System.Windows.Controls;

namespace DocMind.Helpers;

/// <summary>
/// PasswordBox 的双向绑定辅助（PasswordBox.Password 不是依赖属性，无法直接绑定）。
/// 用法：在 XAML 中引用 xmlns:helpers="clr-namespace:DocMind.Helpers"
/// 然后 <PasswordBox helpers:PasswordBoxHelper.BoundPassword="{Binding MyProperty}" />
/// </summary>
public static class PasswordBoxHelper
{
    public static readonly DependencyProperty BoundPasswordProperty =
        DependencyProperty.RegisterAttached(
            "BoundPassword",
            typeof(string),
            typeof(PasswordBoxHelper),
            new FrameworkPropertyMetadata(string.Empty, FrameworkPropertyMetadataOptions.BindsTwoWayByDefault, OnBoundPasswordChanged));

    public static string GetBoundPassword(DependencyObject obj) =>
        (string)obj.GetValue(BoundPasswordProperty);

    public static void SetBoundPassword(DependencyObject obj, string value) =>
        obj.SetValue(BoundPasswordProperty, value ?? string.Empty);

    private static bool _isUpdating;

    private static void OnBoundPasswordChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is not PasswordBox passwordBox || _isUpdating)
            return;

        passwordBox.PasswordChanged -= OnPasswordChanged;
        passwordBox.Password = (e.NewValue as string) ?? string.Empty;
        passwordBox.PasswordChanged += OnPasswordChanged;
    }

    private static void OnPasswordChanged(object sender, RoutedEventArgs e)
    {
        if (sender is not PasswordBox passwordBox)
            return;

        _isUpdating = true;
        SetBoundPassword(passwordBox, passwordBox.Password);
        _isUpdating = false;
    }
}