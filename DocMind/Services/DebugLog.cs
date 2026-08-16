using System.IO;
using System.Text;
using System.Threading;
using System.Collections.Concurrent;

namespace DocMind.Services;

/// <summary>
/// 集中式调试日志：同时写入内存环形缓冲、磁盘日志文件，
/// 并通过事件通知 UI（DebugLogView）实时刷新。
/// 线程安全。日志路径：%LOCALAPPDATA%/DocMind/logs/debug.log
/// </summary>
public static class DebugLog
{
    /// <summary>日志级别。</summary>
    public enum Level
    {
        Debug,
        Info,
        Warn,
        Error,
    }

    /// <summary>新日志条目事件。UI 订阅以增量刷新。</summary>
    public static event Action<string>? LineAppended;

    private static readonly ConcurrentQueue<string> _buffer = new();
    private static readonly int _bufferCapacity = 2000;
    private static readonly object _fileLock = new();
    private static readonly string _logDir = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "DocMind", "logs");
    private static readonly string _logFile = Path.Combine(_logDir, "debug.log");
    private static bool _fileInitFailed;

    /// <summary>单份日志文件大小上限（超过则轮转）。</summary>
    private static readonly long _maxFileSize = 5L * 1024 * 1024;
    /// <summary>轮转保留份数：debug.log + debug.1.log … debug.4.log。</summary>
    private static readonly int _maxLogFiles = 5;
    /// <summary>本次进程累计 ERROR 条数（线程安全）。</summary>
    private static int _errorCount;

    static DebugLog()
    {
        try
        {
            Directory.CreateDirectory(_logDir);
        }
        catch
        {
            _fileInitFailed = true;
        }
    }

    /// <summary>当前内存缓冲中的全部日志行（旧→新）。</summary>
    public static IReadOnlyList<string> Snapshot()
    {
        return _buffer.ToArray();
    }

    public static void Debug(string message, string? category = null)
        => Append(Level.Debug, message, category);

    public static void Info(string message, string? category = null)
        => Append(Level.Info, message, category);

    public static void Warn(string message, string? category = null)
        => Append(Level.Warn, message, category);

    public static void Error(string message, string? category = null, Exception? ex = null)
    {
        if (ex is not null)
        {
            message = $"{message} | {ex.GetType().Name}: {ex.Message}";
        }
        Append(Level.Error, message, category);
    }

    public static void Error(Exception ex, string? category = null, string? context = null)
    {
        var message = context is null
            ? $"{ex.GetType().Name}: {ex.Message}"
            : $"{context} | {ex.GetType().Name}: {ex.Message}";
        Append(Level.Error, message, category);
    }

    /// <summary>记录 HTTP 请求/响应的摘要（截断长 body）。</summary>
    public static void Http(
        string method,
        string uri,
        int status,
        long elapsedMs,
        string? requestBody = null,
        string? responseBody = null,
        string? error = null)
    {
        var sb = new StringBuilder();
        sb.Append("HTTP ").Append(method).Append(' ').Append(uri);
        sb.Append(" -> ").Append(status).Append(" in ").Append(elapsedMs).Append("ms");
        if (requestBody is not null)
        {
            var b = requestBody.Length > 800 ? requestBody[..800] + "…(truncated)" : requestBody;
            sb.Append("\n  req body: ").Append(b);
        }
        if (responseBody is not null)
        {
            var b = responseBody.Length > 800 ? responseBody[..800] + "…(truncated)" : responseBody;
            sb.Append("\n  resp body: ").Append(b);
        }
        if (error is not null)
        {
            sb.Append("\n  error: ").Append(error);
        }
        Append(error is not null ? Level.Error : Level.Info, sb.ToString(), "HTTP");
    }

    private static void Append(Level lvl, string message, string? category)
    {
        var ts = DateTime.Now.ToString("HH:mm:ss.fff");
        var cat = category ?? "APP";
        var lvlStr = lvl.ToString().ToUpperInvariant();
        // 多行 message 缩进对齐
        var lines = message.Replace("\r", "").Split('\n');
        var first = $"[{ts}] {lvlStr} [{cat}] {lines[0]}";
        var entry = first;
        for (var i = 1; i < lines.Length; i++)
        {
            entry += "\n" + new string(' ', 22) + lines[i];
        }

        // 内存环形缓冲
        _buffer.Enqueue(entry);
        while (_buffer.Count > _bufferCapacity)
        {
            _buffer.TryDequeue(out _);
        }

        // 磁盘文件
        if (!_fileInitFailed)
        {
            lock (_fileLock)
            {
                try
                {
                    RotateIfNeeded();
                    File.AppendAllText(_logFile, entry + Environment.NewLine, Encoding.UTF8);
                }
                catch
                {
                    _fileInitFailed = true;
                }
            }
        }

        // 错误计数（供 UI 徽标/统计使用）
        if (lvl == Level.Error)
        {
            Interlocked.Increment(ref _errorCount);
        }

        // UI 通知
        try
        {
            LineAppended?.Invoke(entry);
        }
        catch
        {
            // 忽略订阅者异常
        }
    }

    /// <summary>清空内存缓冲（不影响磁盘文件）。</summary>
    public static void ClearBuffer()
    {
        while (_buffer.TryDequeue(out _)) { }
    }

    /// <summary>返回日志文件的完整路径，方便用户打开查看。</summary>
    public static string LogFilePath => _logFile;

    /// <summary>本次进程累计 ERROR 条数。</summary>
    public static int ErrorCount => Volatile.Read(ref _errorCount);

    /// <summary>日志文件超过大小上限时轮转：debug.log → debug.1.log …（保留 _maxLogFiles 份）。
    /// 必须在持有 _fileLock 时调用。</summary>
    private static void RotateIfNeeded()
    {
        if (!File.Exists(_logFile) || new FileInfo(_logFile).Length <= _maxFileSize)
        {
            return;
        }

        try
        {
            var oldest = Path.Combine(_logDir, $"debug.{_maxLogFiles}.log");
            if (File.Exists(oldest))
            {
                File.Delete(oldest);
            }
            for (var i = _maxLogFiles - 1; i >= 1; i--)
            {
                var src = i == 1 ? _logFile : Path.Combine(_logDir, $"debug.{i - 1}.log");
                var dst = Path.Combine(_logDir, $"debug.{i}.log");
                if (File.Exists(src))
                {
                    File.Move(src, dst, overwrite: true);
                }
            }
        }
        catch
        {
            // 轮转失败不阻塞日志写入
        }
    }

    /// <summary>记录启动横幅：版本、时间、系统信息，便于定位多份日志对应的会话。</summary>
    public static void LogStartup(string version)
    {
        var banner = new StringBuilder();
        banner.Append("==================== DocMind 启动 ====================");
        banner.Append("\n  版本: ").Append(version);
        banner.Append("\n  启动时间: ").Append(DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff"));
        banner.Append("\n  OS: ").Append(Environment.OSVersion.VersionString);
        banner.Append("\n  .NET: ").Append(Environment.Version);
        banner.Append("\n  64位进程: ").Append(Environment.Is64BitProcess);
        banner.Append("\n  命令行: ").Append(Environment.CommandLine);
        banner.Append("\n======================================================");
        Append(Level.Info, banner.ToString(), "APP");
    }

    /// <summary>用资源管理器打开日志目录并选中当前日志文件。</summary>
    public static void OpenLogFolder()
    {
        try
        {
            Directory.CreateDirectory(_logDir);
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = "explorer.exe",
                Arguments = $"/select,\"{_logFile}\"",
                UseShellExecute = true,
            });
        }
        catch (Exception ex)
        {
            Append(Level.Error, $"打开日志目录失败: {ex.Message}", "APP");
        }
    }
}
