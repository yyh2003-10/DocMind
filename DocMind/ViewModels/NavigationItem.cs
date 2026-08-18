using System;
using System.IO;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace DocMind.ViewModels;

public class NavigationItem
{
    public string Title { get; set; } = string.Empty;
    public string Icon { get; set; } = string.Empty;
    public string? IconPath { get; set; }
    public string Category { get; set; } = string.Empty;
    public Type ViewModelType { get; set; } = null!;

    /// <summary>将 IconPath 文件路径转为 ImageSource，供 XAML Image.Source 绑定。</summary>
    public ImageSource? IconSource
    {
        get
        {
            if (string.IsNullOrEmpty(IconPath))
                return null;
            var full = Path.Combine(AppContext.BaseDirectory, IconPath);
            if (!File.Exists(full))
                return null;
            try
            {
                return new BitmapImage(new Uri(full, UriKind.Absolute));
            }
            catch
            {
                return null;
            }
        }
    }
}
