using DocMind.Models;

namespace DocMind.Services;

/// <summary>
/// 通知服务：允许从应用任意位置触发 Toast 通知。
/// ViewModel 通过 DI 获取此实例，调用 Show() 即可。
/// </summary>
public class NotificationService
{
    /// <summary>新通知到达事件（UI 订阅）。</summary>
    public event Action<ToastNotification>? NotificationAdded;

    /// <summary>显示一条通知。</summary>
    public void Show(ToastNotification notification)
    {
        NotificationAdded?.Invoke(notification);
    }

    /// <summary>快捷方法：Info 通知。</summary>
    public void Info(string message, string? title = null)
        => Show(ToastNotification.Info(message, title));

    /// <summary>快捷方法：Success 通知。</summary>
    public void Success(string message, string? title = null)
        => Show(ToastNotification.Success(message, title));

    /// <summary>快捷方法：Warning 通知。</summary>
    public void Warning(string message, string? title = null)
        => Show(ToastNotification.Warning(message, title));

    /// <summary>快捷方法：Error 通知。</summary>
    public void Error(string message, string? title = null)
        => Show(ToastNotification.Error(message, title));
}
