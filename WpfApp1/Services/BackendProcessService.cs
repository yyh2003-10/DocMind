using System.Diagnostics;
using System.IO;
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
    private CancellationTokenSource? _healthMonitorCts;

    /// <summary>后端当前状态（离线 / 启动中 / 在线 / 退出中）。</summary>
    public BackendState State { get; private set; } = BackendState.Offline;

    /// <summary>状态变化事件（供 UI 状态灯订阅）。</summary>
    public event EventHandler<BackendState>? StateChanged;

    /// <summary>实际后端地址变化事件（端口被占用顺延后触发，供 API 客户端同步 BaseAddress）。</summary>
    public event EventHandler<string>? BackendUrlChanged;

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

        // 先探测 URL 上是否已有健康后端（外部实例 / 上次会话遗留的后端仍在线）。
        // 有则直接复用，不再拉起新进程——否则新进程因端口占用立即退出，
        // MonitorAsync 会把状态灯翻回离线，造成"后端明明在线却显示离线"。
        if (await ProbeHealthOnceAsync(ct))
        {
            SetState(BackendState.Online);
            progress?.Report("检测到后端已在线，复用现有实例");
            // 持续健康监控：该实例退出时状态灯翻离线
            StartHealthMonitor();
            return true;
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
        _healthMonitorCts?.Cancel();
        _healthMonitorCts = null;

        await TryCleanupAsync(ct);
        SetState(BackendState.Offline);
    }

    /// <summary>对外暴露当前状态；若未就绪则触发一次健康检查自动更新状态。
    /// 之后启动持续健康轮询：AutoStartBackend=false 接外部后端时，
    /// 外部后端中途退出也能把状态灯翻回离线，而不是永远卡在 Online。</summary>
    public async Task RefreshStateAsync(CancellationToken ct = default)
    {
        // 无论是否拥有子进程都探测健康：
        // AutoStartBackend=false 时 _python 恒为 null（从未 StartAsync），
        // 但仍需探测外部已运行的后端，否则状态灯永远 Offline。
        try
        {
            using var resp = await _healthClient.GetAsync(
                $"{_settings.BackendUrl.TrimEnd('/')}/v1/health", ct);
            SetState(resp.IsSuccessStatusCode
                ? BackendState.Online
                : BackendState.Offline);
        }
        catch
        {
            SetState(BackendState.Offline);
        }

        // 持续监控：每 PollIntervalMs 探测一次，外部后端崩溃/重启都能反映到状态灯。
        // 自拉子进程模式由 MonitorAsync 监控进程退出，此处仅外部模式需要；
        // 统一启动无副作用（重复调用会被字段判重跳过）。
        StartHealthMonitor();
    }

    private void StartHealthMonitor()
    {
        if (_healthMonitorCts is not null)
        {
            return;
        }
        _healthMonitorCts = new CancellationTokenSource();
        _ = HealthMonitorLoopAsync(_healthMonitorCts.Token);
    }

    /// <summary>周期探测后端健康，持续更新状态（Offline/Online 双向联动）。</summary>
    private async Task HealthMonitorLoopAsync(CancellationToken ct)
    {
        var url = $"{_settings.BackendUrl.TrimEnd('/')}/v1/health";
        var interval = TimeSpan.FromMilliseconds(Math.Max(1000, _settings.PollIntervalMs));
        try
        {
            while (!ct.IsCancellationRequested)
            {
                try
                {
                    using var resp = await _healthClient.GetAsync(url, ct);
                    SetState(resp.IsSuccessStatusCode
                        ? BackendState.Online
                        : BackendState.Offline);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch
                {
                    SetState(BackendState.Offline);
                }
                await Task.Delay(interval, ct);
            }
        }
        catch (OperationCanceledException)
        {
            // 主动取消 → 正常退出
        }
    }

    private Process StartPythonProcess()
    {
        // 解析后端命令优先级：
        //   1) 环境变量 DOC2MIND_CMD（绝对路径或命令名）
        //   2) appsettings.BackendCommand（用户在设置页填的绝对路径）
        //   3) 自动探测：where doc2mind → python -m doc2mind → 裸命令名 doc2mind
        var (fileName, argsPrefix) = ResolveBackendCommand();
        var psi = new ProcessStartInfo
        {
            FileName = fileName,
            // 从 BackendUrl 提取 host/port；argsPrefix 用于 `python -m doc2mind` 形式
            Arguments = $"{argsPrefix}serve --host {ExtractHost()} --port {ExtractPort()}",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
        };
        // HuggingFace 镜像配置：优先用设置页的 HfEndpoint，其次保留系统环境变量，
        // 都没配时注入默认 hf-mirror.com（国内网络必需）。
        if (!string.IsNullOrWhiteSpace(_settings.HfEndpoint))
        {
            psi.Environment["HF_ENDPOINT"] = _settings.HfEndpoint.Trim();
        }
        else if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("HF_ENDPOINT")))
        {
            psi.Environment["HF_ENDPOINT"] = "https://hf-mirror.com";
        }

        // 设置页的模型/分块参数 → 后端 DOC2MIND_* 环境变量（重启后端生效）。
        // 只在非空时注入，留空让后端用内置默认值。
        if (!string.IsNullOrWhiteSpace(_settings.EmbedModel))
            psi.Environment["DOC2MIND_EMBED_MODEL"] = _settings.EmbedModel.Trim();
        if (_settings.ChunkMaxTokens is > 0)
            psi.Environment["DOC2MIND_CHUNK_MAX_TOKENS"] = _settings.ChunkMaxTokens.Value.ToString();
        if (_settings.ChunkMinChars is > 0)
            psi.Environment["DOC2MIND_CHUNK_MIN_CHARS"] = _settings.ChunkMinChars.Value.ToString();
        if (_settings.ChunkOverlapChars is > 0)
            psi.Environment["DOC2MIND_CHUNK_OVERLAP_CHARS"] = _settings.ChunkOverlapChars.Value.ToString();
        if (_settings.ChunkMaxChars is > 0)
            psi.Environment["DOC2MIND_CHUNK_MAX_CHARS"] = _settings.ChunkMaxChars.Value.ToString();

        _logger?.LogInformation("启动后端：{Cmd} {Args}", psi.FileName, psi.Arguments);
        return Process.Start(psi) ?? throw new InvalidOperationException("Process.Start 返回 null");
    }

    /// <summary>解析后端命令：返回 (fileName, argsPrefix)。
    /// argsPrefix 非空时用于 `python -m doc2mind` 形式（argsPrefix = "-m doc2mind "）。</summary>
    private (string fileName, string argsPrefix) ResolveBackendCommand()
    {
        // 1) 环境变量覆盖
        var envCmd = Environment.GetEnvironmentVariable("DOC2MIND_CMD");
        if (!string.IsNullOrWhiteSpace(envCmd))
        {
            return (envCmd.Trim(), string.Empty);
        }

        // 2) appsettings.BackendCommand 用户配置
        if (!string.IsNullOrWhiteSpace(_settings.BackendCommand))
        {
            return (_settings.BackendCommand!.Trim(), string.Empty);
        }

        // 3) 项目自带 .venv 优先：从应用目录向上找 .venv，避免命中 PATH 里失效的全局 doc2mind 安装
        var venv = TryResolveProjectVenv();
        if (venv is not null)
        {
            return venv.Value;
        }

        // 4) 自动探测：where doc2mind（Windows 上 where / Git Bash 上 which）
        var resolved = TryResolveFromWhere("doc2mind");
        if (resolved is not null)
        {
            return (resolved, string.Empty);
        }

        // 5) 回退：尝试 python.exe（PATH 里）+ -m doc2mind
        var pythonExe = TryResolveFromWhere("python") ?? "python";
        return (pythonExe, "-m doc2mind ");
    }

    /// <summary>从应用所在目录逐级向上查找项目自带 .venv（最多 6 层），
    /// 命中返回 (fileName, argsPrefix)；找不到返回 null。</summary>
    private static (string fileName, string argsPrefix)? TryResolveProjectVenv()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (int i = 0; i < 6 && dir is not null; i++, dir = dir.Parent)
        {
            if (dir is null)
            {
                break;
            }
            var scripts = Path.Combine(dir.FullName, ".venv", "Scripts");
            if (!Directory.Exists(scripts))
            {
                continue;
            }

            var doc2mindExe = Path.Combine(scripts, "doc2mind.exe");
            if (File.Exists(doc2mindExe))
            {
                return (doc2mindExe, string.Empty);
            }

            var pythonExe = Path.Combine(scripts, "python.exe");
            if (File.Exists(pythonExe))
            {
                return (pythonExe, "-m doc2mind ");
            }
        }
        return null;
    }

    /// <summary>用 where 命令查可执行文件绝对路径；找不到返回 null。</summary>
    private static string? TryResolveFromWhere(string command)
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = "where",
                Arguments = Uri.EscapeDataString(command),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
            };
            using var p = Process.Start(psi);
            if (p is null) return null;
            p.WaitForExit(milliseconds: 2000);
            if (p.ExitCode != 0) return null;
            var line = p.StandardOutput.ReadLine();
            return string.IsNullOrWhiteSpace(line) ? null : line.Trim();
        }
        catch
        {
            return null;
        }
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

    /// <summary>单次健康探测：URL 上是否已有可用的后端实例（外部/遗留实例）。</summary>
    private async Task<bool> ProbeHealthOnceAsync(CancellationToken ct)
    {
        try
        {
            using var resp = await _healthClient.GetAsync(
                $"{_settings.BackendUrl.TrimEnd('/')}/v1/health", ct);
            return resp.IsSuccessStatusCode;
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch
        {
            return false;
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

            // 默认端口探测失败 → 尝试读取后端写的 server.port（端口被占用顺延时生效）
            var actualPort = TryResolveActualPort();
            if (actualPort is not null && actualPort != ExtractPort())
            {
                SwitchBackendUrl(actualPort.Value);
                url = $"{_settings.BackendUrl.TrimEnd('/')}/v1/health";
            }

            progress?.Report("等待后端就绪…");
            await Task.Delay(delay, ct);
        }
        return false;
    }

    /// <summary>读取后端 server.port 状态文件（%LOCALAPPDATA%\doc2mind\server.port），
    /// 返回实际监听端口；文件不存在/损坏时返回 null。</summary>
    private int? TryResolveActualPort()
    {
        try
        {
            var baseDir = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            var portFile = Path.Combine(baseDir, "doc2mind", "server.port");
            if (!File.Exists(portFile))
            {
                return null;
            }
            var text = File.ReadAllText(portFile).Trim();
            return int.TryParse(text, out var port) && port > 0 ? port : null;
        }
        catch
        {
            return null;
        }
    }

    /// <summary>切换后端地址到新端口（仅运行时，不持久化），并广播事件供 API 客户端同步。</summary>
    private void SwitchBackendUrl(int port)
    {
        var baseUrl = _settings.BackendUrl.TrimEnd('/');
        if (Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri) && uri.Port != port)
        {
            var newUrl = $"{uri.Scheme}://{uri.Host}:{port}";
            DebugLog.Info($"后端端口顺延：{uri.Port} → {port}，客户端已跟随", "Backend");
            _settings.BackendUrl = newUrl;
            BackendUrlChanged?.Invoke(this, newUrl);
        }
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
                // 自拉子进程退出。先探测 URL 上是否仍有健康实例在服务：
                // 端口被外部/遗留实例占用时，自拉进程会立即退出，但后端其实在线；
                // 此时保持 Online 并转入健康轮询，而不是盲设 Offline。
                _logger?.LogWarning("后端子进程退出 (code {ExitCode})，重新探测健康状态", proc.ExitCode);
                _python = null;
                if (await ProbeHealthOnceAsync(ct))
                {
                    SetState(BackendState.Online);
                    StartHealthMonitor();
                }
                else
                {
                    SetState(BackendState.Offline);
                }
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
        // 注意：这里绝不终止后端子进程。
        // 是否停止后端由调用方依据 StopBackendOnExit 显式调用 StopAsync 决定；
        // Dispose 只释放本服务的监控与网络资源，避免 StopBackendOnExit=false
        // （退出后保留后端继续运行）时，fire-and-forget 的 StopAsync 仍非确定性地杀掉后端。
        _monitorCts?.Cancel();
        _monitorCts?.Dispose();
        _monitorCts = null;
        _healthMonitorCts?.Cancel();
        _healthMonitorCts?.Dispose();
        _healthMonitorCts = null;
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
