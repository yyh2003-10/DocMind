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
            DebugLog.Debug($"StartAsync 跳过：已在目标状态 (State={State})", "Backend");
            return true;
        }
        if (_python is { HasExited: true })
        {
            _python = null;
        }

        // 先探测 URL 上是否已有健康后端（外部实例 / 上次会话遗留的后端仍在线）。
        // 有则直接复用，不再拉起新进程——否则新进程因端口占用立即退出，
        // MonitorAsync 会把状态灯翻回离线，造成"后端明明在线却显示离线"。
        DebugLog.Debug($"启动后端流程开始: State={State} Url={_settings.BackendUrl}", "Backend");
        
        // 开发与调试环境下：优先清理历史遗留的旧后端孤儿进程，确保实时加载最新 Python 源码
        if (_settings.AutoStartBackend && (_settings.BackendUrl.Contains("127.0.0.1") || _settings.BackendUrl.Contains("localhost")))
        {
            KillProcessOccupyingPort(ExtractPort());
            await Task.Delay(200, ct);
        }
        else if (await ProbeHealthOnceAsync(ct))
        {
            DebugLog.Info("健康探测通过：复用 URL 上已有的后端实例，不拉起子进程", "Backend");
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
                DebugLog.Error($"后端启动超时：{_settings.StartupTimeoutSec}s 内 /v1/health 未就绪", "Backend");
                SetState(BackendState.Offline);
                progress?.Report("后端启动超时");
                await TryCleanupAsync(ct);
            }
            return ready;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger?.LogError(ex, "后端启动失败");
            DebugLog.Error($"后端启动异常: {ex.Message}", "Backend", ex);
            SetState(BackendState.Offline);
            progress?.Report($"后端启动失败：{ex.Message}");
            await TryCleanupAsync(ct);
            return false;
        }
    }

    /// <summary>强力重启后端：彻底清理旧孤儿进程并重新拉起最新子进程。</summary>
    public async Task<bool> RestartAsync(
        IProgress<string>? progress = null,
        CancellationToken ct = default)
    {
        DebugLog.Info("执行后端强力重启...", "Backend");
        await StopAsync(ct);
        KillProcessOccupyingPort(ExtractPort());
        await Task.Delay(500, ct);
        return await StartAsync(progress, ct);
    }

    /// <summary>优雅退出后端：先 stdin 关闭 / 等待 5s，再强制 kill。</summary>
    public async Task StopAsync(CancellationToken ct = default)
    {
        if (State == BackendState.Offline && _python == null)
        {
            KillProcessOccupyingPort(ExtractPort());
            return;
        }
        DebugLog.Info($"停止后端流程开始: State={State} PID={_python?.Id.ToString() ?? "-"}", "Backend");
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
            var actualPort = TryResolveActualPort();
            if (actualPort is not null && actualPort != ExtractPort())
            {
                try
                {
                    var targetUrl = $"{ExtractScheme()}://{ExtractHost()}:{actualPort.Value}/v1/health";
                    using var portResp = await _healthClient.GetAsync(targetUrl, ct);
                    if (portResp.IsSuccessStatusCode)
                    {
                        SwitchBackendUrl(actualPort.Value);
                        SetState(BackendState.Online);
                        StartHealthMonitor();
                        return;
                    }
                }
                catch (OperationCanceledException) { throw; }
                catch { /* fallback */ }
            }

            using var resp = await _healthClient.GetAsync(
                $"{_settings.BackendUrl.TrimEnd('/')}/v1/health", ct);
            SetState(resp.IsSuccessStatusCode
                ? BackendState.Online
                : BackendState.Offline);
            DebugLog.Debug($"外部后端探测: HTTP {(int)resp.StatusCode} → State={State}", "Backend");
        }
        catch (Exception ex)
        {
            DebugLog.Debug($"外部后端探测失败（视为离线）: {ex.GetType().Name}: {ex.Message}", "Backend");
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
        // 记录实际注入的环境变量（均为模型/分块参数，非敏感），便于排查"配置未生效"
        var injectedEnv = new List<string>();
        // HuggingFace 镜像配置：优先用设置页的 HfEndpoint，其次保留系统环境变量，
        // 都没配时注入默认 hf-mirror.com（国内网络必需）。
        if (!string.IsNullOrWhiteSpace(_settings.HfEndpoint))
        {
            psi.Environment["HF_ENDPOINT"] = _settings.HfEndpoint.Trim();
            injectedEnv.Add($"HF_ENDPOINT={psi.Environment["HF_ENDPOINT"]}");
        }
        else if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("HF_ENDPOINT")))
        {
            psi.Environment["HF_ENDPOINT"] = "https://hf-mirror.com";
            injectedEnv.Add("HF_ENDPOINT=https://hf-mirror.com");
        }

        // 设置页的模型/分块参数 → 后端 DOC2MIND_* 环境变量（重启后端生效）。
        // 只在非空时注入，留空让后端用内置默认值。
        if (!string.IsNullOrWhiteSpace(_settings.EmbedModel))
        {
            psi.Environment["DOC2MIND_EMBED_MODEL"] = _settings.EmbedModel.Trim();
            injectedEnv.Add($"DOC2MIND_EMBED_MODEL={psi.Environment["DOC2MIND_EMBED_MODEL"]}");
        }
        if (_settings.ChunkMaxTokens is > 0)
        {
            psi.Environment["DOC2MIND_CHUNK_MAX_TOKENS"] = _settings.ChunkMaxTokens.Value.ToString();
            injectedEnv.Add($"DOC2MIND_CHUNK_MAX_TOKENS={psi.Environment["DOC2MIND_CHUNK_MAX_TOKENS"]}");
        }
        if (_settings.ChunkMinChars is > 0)
        {
            psi.Environment["DOC2MIND_CHUNK_MIN_CHARS"] = _settings.ChunkMinChars.Value.ToString();
            injectedEnv.Add($"DOC2MIND_CHUNK_MIN_CHARS={psi.Environment["DOC2MIND_CHUNK_MIN_CHARS"]}");
        }
        if (_settings.ChunkOverlapChars is > 0)
        {
            psi.Environment["DOC2MIND_CHUNK_OVERLAP_CHARS"] = _settings.ChunkOverlapChars.Value.ToString();
            injectedEnv.Add($"DOC2MIND_CHUNK_OVERLAP_CHARS={psi.Environment["DOC2MIND_CHUNK_OVERLAP_CHARS"]}");
        }
        if (_settings.ChunkMaxChars is > 0)
        {
            psi.Environment["DOC2MIND_CHUNK_MAX_CHARS"] = _settings.ChunkMaxChars.Value.ToString();
            injectedEnv.Add($"DOC2MIND_CHUNK_MAX_CHARS={psi.Environment["DOC2MIND_CHUNK_MAX_CHARS"]}");
        }

        // 设置页的 LLM 配置 → DOC2MIND_LLM_* 环境变量（双保险之一）：
        // POST /v1/config 即时推送失败时，后端重启后仍能从环境变量拿到正确配置。
        // 环境变量优先级高于 config.toml（后端 from_env 覆盖语义），与「前端为配置源」一致。
        // API Key 不记入 injectedEnv 日志（敏感值只记「已注入」）。
        if (!string.IsNullOrWhiteSpace(_settings.LlmProvider))
        {
            psi.Environment["DOC2MIND_LLM_PROVIDER"] = _settings.LlmProvider.Trim();
            injectedEnv.Add($"DOC2MIND_LLM_PROVIDER={psi.Environment["DOC2MIND_LLM_PROVIDER"]}");
        }
        if (!string.IsNullOrWhiteSpace(_settings.LlmApiKey))
        {
            // App.LoadSettings 正常已把单例解密为明文；此处再过一次 Unprotect 防御
            // （幂等：明文原样返回），避免单例意外持密文时把 dpapi:v1:… 注进环境变量
            psi.Environment["DOC2MIND_LLM_API_KEY"] = SecretProtector.Unprotect(_settings.LlmApiKey)!.Trim();
            injectedEnv.Add("DOC2MIND_LLM_API_KEY=<已注入，值略>");
        }
        if (!string.IsNullOrWhiteSpace(_settings.LlmBaseUrl))
        {
            psi.Environment["DOC2MIND_LLM_BASE_URL"] = _settings.LlmBaseUrl.Trim();
            injectedEnv.Add($"DOC2MIND_LLM_BASE_URL={psi.Environment["DOC2MIND_LLM_BASE_URL"]}");
        }
        if (!string.IsNullOrWhiteSpace(_settings.LlmModel))
        {
            psi.Environment["DOC2MIND_LLM_MODEL"] = _settings.LlmModel.Trim();
            injectedEnv.Add($"DOC2MIND_LLM_MODEL={psi.Environment["DOC2MIND_LLM_MODEL"]}");
        }
        psi.Environment["DOC2MIND_LLM_TEMPERATURE"] = _settings.LlmTemperature.ToString(System.Globalization.CultureInfo.InvariantCulture);
        injectedEnv.Add($"DOC2MIND_LLM_TEMPERATURE={psi.Environment["DOC2MIND_LLM_TEMPERATURE"]}");
        psi.Environment["DOC2MIND_LLM_MAX_TOKENS"] = _settings.LlmMaxTokens.ToString();
        injectedEnv.Add($"DOC2MIND_LLM_MAX_TOKENS={psi.Environment["DOC2MIND_LLM_MAX_TOKENS"]}");
        psi.Environment["DOC2MIND_RAG_TOP_K"] = _settings.RagTopK.ToString();
        injectedEnv.Add($"DOC2MIND_RAG_TOP_K={psi.Environment["DOC2MIND_RAG_TOP_K"]}");
        if (!string.IsNullOrWhiteSpace(_settings.RagSystemPrompt))
        {
            psi.Environment["DOC2MIND_RAG_SYSTEM_PROMPT"] = _settings.RagSystemPrompt.Trim();
            injectedEnv.Add("DOC2MIND_RAG_SYSTEM_PROMPT=<已注入，值略>");
        }
        psi.Environment["DOC2MIND_RAG_MAX_HISTORY_TOKENS"] = _settings.RagMaxHistoryTokens.ToString();
        injectedEnv.Add($"DOC2MIND_RAG_MAX_HISTORY_TOKENS={psi.Environment["DOC2MIND_RAG_MAX_HISTORY_TOKENS"]}");

        if (_settings.WatchPaths != null && _settings.WatchPaths.Count > 0)
        {
            var validPaths = _settings.WatchPaths.Where(p => !string.IsNullOrWhiteSpace(p)).Select(p => p.Trim());
            var joined = string.Join(",", validPaths);
            if (!string.IsNullOrEmpty(joined))
            {
                psi.Environment["DOC2MIND_WATCH_PATHS"] = joined;
                injectedEnv.Add($"DOC2MIND_WATCH_PATHS={joined}");
            }
        }
        psi.Environment["DOC2MIND_WATCH_DEBOUNCE_SECONDS"] = _settings.WatchDebounceSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture);
        injectedEnv.Add($"DOC2MIND_WATCH_DEBOUNCE_SECONDS={psi.Environment["DOC2MIND_WATCH_DEBOUNCE_SECONDS"]}");

        DebugLog.Info($"启动后端子进程: {psi.FileName} {psi.Arguments}", "Backend");
        DebugLog.Debug($"注入环境变量 ({injectedEnv.Count} 项): {string.Join("; ", injectedEnv)}", "Backend");

        // 注入 PYTHONPATH=仓库 src 目录：让 `python -m doc2mind` 优先 import 仓库内
        // 修复版源码（src/doc2mind），而非 site-packages 里的旧版 doc2mind 发布包，
        // 否则会出现 chat.py 中 `name 'KnowledgeBase' is not defined` 等旧版缺陷。
        var srcDir = TryResolveRepoSrcDir();
        if (srcDir is not null)
        {
            var hasPath = psi.Environment.TryGetValue("PYTHONPATH", out var cur) && !string.IsNullOrEmpty(cur);
            psi.Environment["PYTHONPATH"] = hasPath
                ? $"{srcDir}{Path.PathSeparator}{cur}"
                : srcDir;
            DebugLog.Info($"注入 PYTHONPATH（优先仓库源码）: {psi.Environment["PYTHONPATH"]}", "Backend");
        }

        _logger?.LogInformation("启动后端：{Cmd} {Args}", psi.FileName, psi.Arguments);
        var proc = Process.Start(psi) ?? throw new InvalidOperationException("Process.Start 返回 null");
        
        proc.OutputDataReceived += (_, e) =>
        {
            if (!string.IsNullOrWhiteSpace(e.Data))
            {
                DebugLog.Info($"[PyStdOut] {e.Data}", "Backend");
            }
        };
        proc.ErrorDataReceived += (_, e) =>
        {
            if (!string.IsNullOrWhiteSpace(e.Data))
            {
                DebugLog.Warn($"[PyStdErr] {e.Data}", "Backend");
            }
        };
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();

        DebugLog.Info($"后端子进程已启动: PID={proc.Id}", "Backend");
        return proc;
    }

    /// <summary>解析后端命令：返回 (fileName, argsPrefix)。
    /// argsPrefix 非空时用于 `python -m doc2mind` 形式（argsPrefix = "-m doc2mind "）。</summary>
    private (string fileName, string argsPrefix) ResolveBackendCommand()
    {
        // 1) 环境变量覆盖
        var envCmd = Environment.GetEnvironmentVariable("DOC2MIND_CMD");
        if (!string.IsNullOrWhiteSpace(envCmd))
        {
            DebugLog.Info($"后端命令解析: 来源=环境变量 DOC2MIND_CMD → '{envCmd.Trim()}'", "Backend");
            return (envCmd.Trim(), string.Empty);
        }

        // 2) appsettings.BackendCommand 用户配置
        //    若配置的是 python 解释器（如 python.exe），则补 `-m doc2mind` 前缀，
        //    配合 StartPythonProcess 注入的 PYTHONPATH=src，优先运行仓库内修复版源码
        //    （而非 site-packages 里的旧版 doc2mind 发布包）。
        if (!string.IsNullOrWhiteSpace(_settings.BackendCommand))
        {
            var cmd = _settings.BackendCommand!.Trim();
            var fileName = cmd;
            var argsPrefix = string.Empty;
            if (Path.GetFileName(cmd).Equals("python.exe", StringComparison.OrdinalIgnoreCase)
                || Path.GetFileName(cmd).Equals("python", StringComparison.OrdinalIgnoreCase)
                || Path.GetFileName(cmd).Equals("python3", StringComparison.OrdinalIgnoreCase)
                || Path.GetFileName(cmd).Equals("python3.exe", StringComparison.OrdinalIgnoreCase))
            {
                argsPrefix = "-m doc2mind ";
            }
            DebugLog.Info($"后端命令解析: 来源=用户配置 BackendCommand → '{fileName}' '{argsPrefix}'", "Backend");
            return (fileName, argsPrefix);
        }

        // 3) 项目自带 .venv 优先：从应用目录向上找 .venv，避免命中 PATH 里失效的全局 doc2mind 安装
        var venv = TryResolveProjectVenv();
        if (venv is not null)
        {
            DebugLog.Info($"后端命令解析: 来源=项目 .venv → '{venv.Value.fileName}' '{venv.Value.argsPrefix}'", "Backend");
            return venv.Value;
        }

        // 4) 自动探测：where doc2mind（Windows 上 where / Git Bash 上 which）
        var resolved = TryResolveFromWhere("doc2mind");
        if (resolved is not null)
        {
            DebugLog.Info($"后端命令解析: 来源=where 探测 → '{resolved}'", "Backend");
            return (resolved, string.Empty);
        }

        // 5) 回退：尝试 python.exe（PATH 里）+ -m doc2mind
        var pythonExe = TryResolveFromWhere("python") ?? "python";
        DebugLog.Warn($"后端命令解析: 未找到 doc2mind，回退 '{pythonExe}' -m doc2mind", "Backend");
        return (pythonExe, "-m doc2mind ");
    }

    /// <summary>从应用所在目录逐级向上查找仓库 src 目录（含 doc2mind 包的 Python 源码），
    /// 命中返回绝对路径，找不到返回 null。最多向上 6 层。</summary>
    private static string? TryResolveRepoSrcDir()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (int i = 0; i < 6 && dir is not null; i++, dir = dir.Parent)
        {
            var src = Path.Combine(dir.FullName, "src");
            if (Directory.Exists(src) && Directory.Exists(Path.Combine(src, "doc2mind")))
            {
                return Path.GetFullPath(src);
            }
        }
        return null;
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

            var pythonExe = Path.Combine(scripts, "python.exe");
            if (File.Exists(pythonExe))
            {
                return (pythonExe, "-m doc2mind ");
            }

            var doc2mindExe = Path.Combine(scripts, "doc2mind.exe");
            if (File.Exists(doc2mindExe))
            {
                return (doc2mindExe, string.Empty);
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

    private string ExtractScheme()
    {
        try
        {
            var u = new Uri(_settings.BackendUrl);
            return u.Scheme;
        }
        catch
        {
            return "http";
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
        // 优先探测 server.port 记录的端口（若有新后端因端口顺延启动，优先对齐最新实例）
        var actualPort = TryResolveActualPort();
        if (actualPort is not null && actualPort != ExtractPort())
        {
            try
            {
                var targetUrl = $"{ExtractScheme()}://{ExtractHost()}:{actualPort.Value}/v1/health";
                using var portResp = await _healthClient.GetAsync(targetUrl, ct);
                if (portResp.IsSuccessStatusCode)
                {
                    SwitchBackendUrl(actualPort.Value);
                    return true;
                }
            }
            catch (OperationCanceledException) { throw; }
            catch { /* fallback */ }
        }

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
                DebugLog.Warn($"后端子进程意外退出: PID={proc.Id} exitCode={proc.ExitCode}，重新探测健康状态", "Backend");
                _python = null;
                if (await ProbeHealthOnceAsync(ct))
                {
                    DebugLog.Info("子进程退出后探测到健康实例，保持在线（端口可能被外部实例占用）", "Backend");
                    SetState(BackendState.Online);
                    StartHealthMonitor();
                }
                else
                {
                    DebugLog.Warn("子进程退出且无健康实例，状态置为离线", "Backend");
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
                DebugLog.Warn($"后端 5s 未优雅退出，强制 kill 进程树 (PID {proc.Id})", "Backend");
                try { proc.Kill(entireProcessTree: true); } catch { /* ignore */ }
                try { await proc.WaitForExitAsync(ct); } catch { /* ignore */ }
            }
        }
        finally
        {
            try { proc.Dispose(); } catch { /* ignore */ }
            _python = null;
            KillProcessOccupyingPort(ExtractPort());
        }
    }

    /// <summary>强力清理占用指定端口的孤儿进程（彻底杜绝后台僵尸进程残留霸占端口）。</summary>
    public static void KillProcessOccupyingPort(int port)
    {
        try
        {
            if (OperatingSystem.IsWindows())
            {
                var cmd = $"/c for /f \"tokens=5\" %a in ('netstat -aon ^| findstr \":{port} \" ^| findstr \"LISTENING\"') do taskkill /f /pid %a";
                var psi = new ProcessStartInfo
                {
                    FileName = "cmd.exe",
                    Arguments = cmd,
                    CreateNoWindow = true,
                    UseShellExecute = false,
                };
                using var p = Process.Start(psi);
                p?.WaitForExit(3000);
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"尝试释放端口 {port} 异常: {ex.Message}", "Backend");
        }
    }

    private void SetState(BackendState s)
    {
        if (State == s)
        {
            return;
        }
        DebugLog.Debug($"后端状态变更: {State} → {s}", "Backend");
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
