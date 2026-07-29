using System;

namespace DocMind.ViewModels;

/// <summary>
/// Represents a navigation item in the sidebar
/// </summary>
public class NavigationItem
{
    public string Title { get; set; } = string.Empty;
    public string Icon { get; set; } = string.Empty;
    public Type ViewModelType { get; set; } = null!;
}
