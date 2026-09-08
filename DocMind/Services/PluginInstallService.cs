using System.Diagnostics;
using System.IO;
using Microsoft.Extensions.Logging;

namespace DocMind.Services;

/// <summary>
/// 插件（GPU 加速 / OCR 组件）独立进程安装服务。
///
/// 背景：后端服务进程运行时已加载 numpy / onnxruntime / paddle 等运行库 DLL，
/// Windows 不允许删除被映射的 DLL——在后端进程内跑 pip 安装 paddlepaddle
/// （需要替换 numpy）必然报 WinError 5。本服务在调用方先停止后端之后，
/// 用解析出的 python 解释器以独立进程运行 <c>python -m doc2mind.install_cli</c>，
/// 进程内无任何已加载的运行库，pip 可安全替换文件。
/// </summary>
public interface IPluginInstallService
{
    /// <summary>是否具备独立安装条件（能解析到可用的 python 解释器）。不满足时调用方回退后端 SSE 安装。</summary>
    bool CanInstallOutOfProcess { get; }

    /// <summary>执行安装。返回 true 表示安装成功；取消时抛 OperationCanceledException。</summary>
    Task<bool> InstallAsync(string path, Action<string>? onLog, CancellationToken ct);
}

public sealed class PluginInstallService : IPluginInstallService
{
    private readonly BackendProcessService _backendService;
    private readonly Microsoft.Extensions.Logging.ILogger<PluginInstallService>? _logger;

    public PluginInstallService(
        BackendProcessService backendService,
        Microsoft.Extensions.Logging.ILogger<PluginInstallService>? logger = null)
    {
        _backendService = backendService;
        _logger = logger;
    }

    public bool CanInstallOutOfProcess => _backendService.TryGetPythonExecutable(out _, out _);

    public async Task<bool> InstallAsync(string path, Action<string>? onLog, CancellationToken ct)
    {
        if (!_backendService.TryGetPythonExecutable(out var pythonExe, out var srcDir))
        {
            onLog?.Invoke("[错误] 未找到可用的 Python 解释器，无法独立安装。请检查设置页「后端命令」配置。");
            return false;
        }

        var psi = new ProcessStartInfo
        {
            FileName = pythonExe,
            Arguments = $"-m doc2mind.install_cli {path}",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
        };
        // 与后端启动一致：仓库源码目录优先（开发/调试形态下 doc2mind 源码不在 site-packages）
        if (!string.IsNullOrEmpty(srcDir))
        {
            var hasPath = psi.Environment.TryGetValue("PYTHONPATH", out var cur) && !string.IsNullOrEmpty(cur);
            psi.Environment["PYTHONPATH"] = hasPath ? $"{srcDir}{Path.PathSeparator}{cur}" : srcDir!;
        }
        // CLI 的 stdout 强制 UTF-8（install_cli 内部也 reconfigure，双保险）；
        // C# 端必须按 UTF-8 解码，否则 GBK 默认解码会把 UTF-8 中文读成乱码
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.StandardOutputEncoding = System.Text.Encoding.UTF8;
        psi.StandardErrorEncoding = System.Text.Encoding.UTF8;

        _logger?.LogInformation("独立安装 {Path}：{Cmd} {Args}", path, psi.FileName, psi.Arguments);
        DebugLog.Info($"独立安装启动: {psi.FileName} {psi.Arguments}", "Install");

        using var proc = Process.Start(psi);
        if (proc is null)
        {
            onLog?.Invoke("[错误] 安装进程启动失败。");
            return false;
        }

        proc.OutputDataReceived += (_, e) =>
        {
            if (!string.IsNullOrWhiteSpace(e.Data))
            {
                onLog?.Invoke(e.Data);
            }
        };
        proc.ErrorDataReceived += (_, e) =>
        {
            if (!string.IsNullOrWhiteSpace(e.Data))
            {
                onLog?.Invoke(e.Data);
            }
        };
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();

        try
        {
            await proc.WaitForExitAsync(ct).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            DebugLog.Warn($"独立安装被取消，终止进程树 (PID {proc.Id})", "Install");
            try { proc.Kill(entireProcessTree: true); } catch { /* ignore */ }
            throw;
        }

        var exitCode = proc.ExitCode;
        DebugLog.Info($"独立安装退出: code={exitCode}", "Install");
        if (exitCode != 0)
        {
            onLog?.Invoke($"[错误] 安装进程退出码 {exitCode}，请查看上方日志排查。");
        }
        return exitCode == 0;
    }
}
