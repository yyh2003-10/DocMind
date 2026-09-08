namespace DocMind.Tests;

using System;
using System.Threading;
using System.Threading.Tasks;
using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using Xunit;

/// <summary>
/// 插件安装服务的可控假实现：按脚本返回成功/失败/挂起，并记录调用参数。
/// </summary>
public sealed class FakePluginInstallService : IPluginInstallService
{
    /// <summary>独立安装条件（默认具备，测试回退 SSE 路径时置 false）。</summary>
    public bool CanInstallOutOfProcessValue { get; set; } = true;

    /// <summary>InstallAsync 的返回值。</summary>
    public bool InstallResult { get; set; } = true;

    /// <summary>置 true 时 InstallAsync 挂起直至令牌取消（模拟 pip 卡死）。</summary>
    public bool HangUntilCancelled { get; set; }

    /// <summary>InstallAsync 结束前发出的事件（配合信号量做断言时序）。</summary>
    public Action<string>? LogSink { get; set; }

    public bool CanInstallOutOfProcess => CanInstallOutOfProcessValue;

    public int InstallCalls { get; private set; }

    public string? LastInstallPath { get; private set; }

    public Task<bool> InstallAsync(string path, Action<string>? onLog, CancellationToken ct)
    {
        InstallCalls++;
        LastInstallPath = path;
        LogSink = onLog;
        if (HangUntilCancelled)
        {
            // 模拟 pip 挂死：等待取消令牌，然后按预期抛 OperationCanceledException
            var tcs = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            ct.Register(() => tcs.TrySetCanceled(ct));
            return tcs.Task;
        }
        onLog?.Invoke("[模拟] 安装输出");
        return Task.FromResult(InstallResult);
    }
}

/// <summary>GpuWarningViewModel 安装流程状态机：停后端 → 独立安装 → 恢复后端。</summary>
public sealed class GpuWarningViewModelTests
{
    private static (GpuWarningViewModel Vm, FakeDoc2kbApiService Fake, FakePluginInstallService Installer)
        CreateVm(bool outOfProcess = true)
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetGpuDiagnosis ??= _ =>
            Task.FromResult(new GpuDiagnosis { RecommendedPath = "cpu" });
        var appSettings = new AppSettings();
        var backend = new BackendProcessService(appSettings);
        var installer = new FakePluginInstallService { CanInstallOutOfProcessValue = outOfProcess };
        var vm = new GpuWarningViewModel(fake, backend, installer, new NotificationService());
        return (vm, fake, installer);
    }

    [Fact]
    public void InstallButtonText_FollowsSelectedPath()
    {
        var (vm, _, _) = CreateVm();
        Assert.Equal("🚀 一键安装", vm.InstallButtonText);
        vm.SelectedPath = "ocr-cpu";
        Assert.Equal("⬇ 安装 OCR 组件", vm.InstallButtonText);
        vm.SelectedPath = "paddle-ocr-gpu";
        Assert.Equal("⬇ 安装 OCR 组件", vm.InstallButtonText);
        vm.SelectedPath = "cuda12";
        Assert.Equal("🚀 一键安装 GPU 加速", vm.InstallButtonText);
    }

    [Fact]
    public async Task Install_StopsBackend_InstallsOutOfProcess_RestartsBackend()
    {
        var (vm, _, installer) = CreateVm();
        vm.SelectedPath = "ocr-cpu";

        await vm.InstallGpuCommand.ExecuteAsync(null);

        Assert.Equal(1, installer.InstallCalls);
        Assert.Equal("ocr-cpu", installer.LastInstallPath);
        Assert.True(vm.CanRestart, "安装成功后应可重启/已自动重启");
        Assert.False(vm.IsInstalling);
        // 安装成功后会自动恢复后端并重新诊断，终态应为诊断结果而非安装中态
        Assert.Equal("未启用 GPU 加速", vm.StatusMessage);
    }

    [Fact]
    public async Task Install_Failure_StillRestartsBackend_AndClearsInstalling()
    {
        var (vm, _, installer) = CreateVm();
        installer.InstallResult = false;
        vm.SelectedPath = "cuda12";

        await vm.InstallGpuCommand.ExecuteAsync(null);

        Assert.Equal(1, installer.InstallCalls);
        Assert.False(vm.IsInstalling);
        Assert.False(vm.CanRestart);
        // 失败终态必须保留，不能被"正在恢复后端"中间态覆盖
        Assert.Contains("安装失败", vm.StatusMessage);
    }

    [Fact]
    public async Task Install_HangingProcess_CanBeCancelled()
    {
        var (vm, _, installer) = CreateVm();
        installer.HangUntilCancelled = true;
        vm.SelectedPath = "ocr-cpu";

        var run = vm.InstallGpuCommand.ExecuteAsync(null);
        // 等安装真正进入 IsInstalling（后台线程信号）
        for (var i = 0; i < 100 && !vm.IsInstalling; i++)
        {
            await Task.Delay(20);
        }
        Assert.True(vm.CanCancelInstall);

        vm.CancelInstallCommand.Execute(null);
        await run;

        Assert.False(vm.IsInstalling);
        Assert.False(vm.CanCancelInstall);
        Assert.Contains("取消", vm.StatusMessage);
    }

    [Fact]
    public async Task Install_WithoutOutOfProcess_FallsBackToBackendSse()
    {
        var (vm, fake, installer) = CreateVm(outOfProcess: false);
        vm.SelectedPath = "ocr-cpu";
        var sseCalled = false;
        fake.OnInstallOcr ??= (path, onLog, onDone, _) =>
        {
            sseCalled = true;
            onLog("[SSE] 安装");
            onDone(true);
            return Task.CompletedTask;
        };

        await vm.InstallGpuCommand.ExecuteAsync(null);

        Assert.Equal(0, installer.InstallCalls);
        Assert.True(sseCalled);
        Assert.False(vm.IsInstalling);
    }

    [Fact]
    public async Task Install_CpuPath_IsRejected()
    {
        var (vm, _, installer) = CreateVm();
        vm.SelectedPath = "cpu";

        await vm.InstallGpuCommand.ExecuteAsync(null);

        Assert.Equal(0, installer.InstallCalls);
        Assert.False(vm.IsInstalling);
    }
}
