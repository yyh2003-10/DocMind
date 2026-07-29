namespace DocMind.Models;

/// <summary>通知类型，决定图标和颜色。</summary>
public enum ToastType
{
    Info,
    Success,
    Warning,
    Error,
}

/// <summary>一条通知消息。</summary>
public class ToastNotification
{
    public string Message { get; init; } = string.Empty;
    public string? Title { get; init; }
    public ToastType Type { get; init; } = ToastType.Info;
    public int DurationMs { get; init; } = 3000;
    public Guid Id { get; } = Guid.NewGuid();

    public static ToastNotification Info(string message, string? title = null, int durationMs = 3000)
        => new() { Message = message, Title = title, Type = ToastType.Info, DurationMs = durationMs };

    public static ToastNotification Success(string message, string? title = null, int durationMs = 3000)
        => new() { Message = message, Title = title, Type = ToastType.Success, DurationMs = durationMs };

    public static ToastNotification Warning(string message, string? title = null, int durationMs = 4000)
        => new() { Message = message, Title = title, Type = ToastType.Warning, DurationMs = durationMs };

    public static ToastNotification Error(string message, string? title = null, int durationMs = 5000)
        => new() { Message = message, Title = title, Type = ToastType.Error, DurationMs = durationMs };
}
