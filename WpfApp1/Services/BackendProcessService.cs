using System.Diagnostics;
using System.Net.Http;
using System.Threading;
using Microsoft.Extensions.Logging;

namespace DocMind.Services;

/// <summary>
/// 管理后端 `doc2mind serve` 子进程：启动轮询健康、优雅退出。
/// </summary>
public sealed class BackendProcessService : IDisposable
{
    private readonly AppSettings _settings;
    private readonly ILogger<BackendProcessService>? _logger;
    private readonly HttpClient _healthClient;
    private Process? _python;
    private CancellationTokenSource? _monitorCts;

    /// <summary>后端当前状态（离线 / 启动中 / 在线 / 退出中）。</summary>
    public BackendState State { get; private set; } = BackendState.Offline;

    /// <summary>状态变化事件（供 UI 状态灯订阅）。</summary>
    public event EventHandler<BackendState>? StateChanged;

    public BackendProcessService(
        AppSettings settings,
        ILogger<BackendProcessService>? logger = null)
    {
        _settings = settings;
        _logger = logger;
        _healthClient = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(3),
        };
    }

    /// <summary>拉起后端子进程并轮询 /v1/health 直至就绪。</summary>
    /// <param name="progress">可选进度推送（用于启动日志）。</param>
    /// <param name="ct">取消令牌。</param>
    /// <returns>是否在 StartupTimeoutSec 内就绪。</returns>
    public async Task<bool> StartAsync(
        IProgress<string>? progress = null,
        CancellationToken ct = default)
    {
        if (State == BackendState.Online || _python is { HasExited: false })
        {
            return true;
        }
        if (_python is { HasExited: true })
        {
            _python = null;
        }

        SetState(BackendState.Starting);
        progress?.Report("正在启动后端…");

        try
        {
            _python = StartPythonProcess();
            progress?.Report($"后端子进程已启动 (PID {_python.Id})，等待就绪…");

            var ready = await PollHealthAsync(
                TimeSpan.FromSeconds(_settings.StartupTimeoutSec),
                progress,
                ct);

            if (ready)
            {
                SetState(BackendState.Online);
                progress?.Report("后端已就绪");
                // 启动后台监控：进程意外退出时更新状态
                _monitorCts = new CancellationTokenSource();
                _ = MonitorAsync(_monitorCts.Token);
            }
            else
            {
                SetState(BackendState.Offline);
                progress?.Report("后端启动超时");
                await TryCleanupAsync(ct);
            }
            return ready;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger?.LogError(ex, "后端启动失败");
            SetState(BackendState.Offline);
            progress?.Report($"后端启动失败：{ex.Message}");
            await TryCleanupAsync(ct);
            return false;
        }
    }

    /// <summary>优雅退出后端：先 stdin 关闭 / 等待 5s，再强制 kill。</summary>
    public async Task StopAsync(CancellationToken ct = default)
    {
        if (State == BackendState.Offline)
        {
            return;
        }
        SetState(BackendState.Stopping);
        _monitorCts?.Cancel();
        _monitorCts = null;

        await TryCleanupAsync(ct);
        SetState(BackendState.Offline);
    }

    /// <summary>对外暴露当前状态；若未就绪则触发一次健康检查自动更新状态。</summary>
    public async Task RefreshStateAsync(CancellationToken ct = default)
    {
        if (_python is null or { HasExited: true })
        {
            SetState(BackendState.Offline);
            return;
        }
        try
        {
            using var resp = await _healthClient.GetAsync(
                $"{_settings.BackendUrl.TrimEnd('/')}/v1/health", ct);
            SetState(resp.IsSuccessStatusCode
                ? BackendState.Online
                : BackendState.Starting);
        }
        catch
        {
            SetState(BackendState.Starting);
        }
    }

    private Process StartPythonProcess()
    {
        // 优先环境变量覆盖；其次假设 doc2mind 已在 PATH
        var cmd = Environment.GetEnvironmentVariable("DOC2MIND_CMD") ?? "doc2mind";
        var psi = new ProcessStartInfo
        {
            FileName = cmd,
            // 从 BackendUrl 提取 host/port
            Arguments = $"serve --host {ExtractHost()} --port {ExtractPort()}",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
        };
        _logger?.LogInformation("启动后端：{Cmd} {Args}", psi.FileName, psi.Arguments);
        return Process.Start(psi) ?? throw new InvalidOperationException("Process.Start 返回 null");
    }

    private string ExtractHost()
    {
        // BackendUrl 形如 http://127.0.0.1:8765 → 127.0.0.1
        try
        {
            var u = new Uri(_settings.BackendUrl);
            return u.Host;
        }
        catch
        {
            return "127.0.0.1";
        }
    }

    private int ExtractPort()
    {
        try
        {
            var u = new Uri(_settings.BackendUrl);
            return u.Port;
        }
        catch
        {
            return 8765;
        }
    }

    private async Task<bool> PollHealthAsync(
        TimeSpan timeout,
        IProgress<string>? progress,
        CancellationToken ct)
    {
        var deadline = DateTimeOffset.UtcNow + timeout;
        var url = $"{_settings.BackendUrl.TrimEnd('/')}/v1/health";
        var delay = Math.Max(200, _settings.PollIntervalMs);

        while (DateTimeOffset.UtcNow < deadline)
        {
            ct.ThrowIfCancellationRequested();
            try
            {
                using var resp = await _healthClient.GetAsync(url, ct);
                if (resp.IsSuccessStatusCode)
                {
                    return true;
                }
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
            {
                // 启动中，端口未就绪 → 继续
            }
            progress?.Report("等待后端就绪…");
            await Task.Delay(delay, ct);
        }
        return false;
    }

    private async Task MonitorAsync(CancellationToken ct)
    {
        var proc = _python;
        if (proc is null)
        {
            return;
        }
        try
        {
            await proc.WaitForExitAsync(ct);
            if (!ct.IsCancellationRequested)
            {
                // 非主动退出 → 标记离线
                _logger?.LogWarning("后端子进程意外退出 (code {ExitCode})", proc.ExitCode);
                SetState(BackendState.Offline);
            }
        }
        catch (OperationCanceledException)
        {
            // 主动 StopAsync 触发 → 忽略
        }
    }

    private async Task TryCleanupAsync(CancellationToken ct)
    {
        var proc = _python;
        if (proc is null or { HasExited: true })
        {
            _python = null;
            return;
        }

        try
        {
            // 先优雅：关闭 stdin 让 doc2mind serve 收到 EOF 自退出
            try { proc.StandardInput.Close(); } catch { /* ignore */ }
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            cts.CancelAfter(TimeSpan.FromSeconds(5));
            try
            {
                await proc.WaitForExitAsync(cts.Token);
            }
            catch (OperationCanceledException)
            {
                // 5s 未退 → 强制 kill
                try { proc.Kill(entireProcessTree: true); } catch { /* ignore */ }
                try { await proc.WaitForExitAsync(ct); } catch { /* ignore */ }
            }
        }
        finally
        {
            try { proc.Dispose(); } catch { /* ignore */ }
            _python = null;
        }
    }

    private void SetState(BackendState s)
    {
        if (State == s)
        {
            return;
        }
        State = s;
        StateChanged?.Invoke(this, s);
    }

    public void Dispose()
    {
        _monitorCts?.Cancel();
        _monitorCts?.Dispose();
        _ = StopAsync(CancellationToken.None);
        _healthClient.Dispose();
    }
}

/// <summary>后端状态枚举。</summary>
public enum BackendState
{
    Offline,
    Starting,
    Online,
    Stopping,
}
