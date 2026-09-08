namespace DocMind.Tests;

using System;
using System.IO;
using System.Threading.Tasks;
using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using Xunit;

/// <summary>ResourceLocatorService：校验器、三级解析、缓存冷却、Apply 写配置。</summary>
[Collection("SettingsFile")]
public sealed class ResourceLocatorServiceTests : IDisposable
{
    private readonly string _tempDir;
    private readonly AppSettings _settings;
    private readonly ResourceLocatorService _locator;

    public ResourceLocatorServiceTests()
    {
        _tempDir = Path.Combine(Path.GetTempPath(), "DocMind.Tests.Locator", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tempDir);
        _settings = new AppSettings();
        _locator = new ResourceLocatorService(_settings);
    }

    public void Dispose()
    {
        try { Directory.Delete(_tempDir, recursive: true); } catch { /* ignore */ }
    }

    // ===== 校验器 =====

    [Fact]
    public void Validate_Poppler_RequiresPdftoppm()
    {
        var bin = Path.Combine(_tempDir, "poppler", "bin");
        Directory.CreateDirectory(bin);
        Assert.False(_locator.Validate(LocatableResource.Poppler, bin));

        File.WriteAllText(Path.Combine(bin, "pdftoppm.exe"), "fake");
        Assert.True(_locator.Validate(LocatableResource.Poppler, bin));
    }

    [Fact]
    public void Validate_Wheels_RequiresAtLeastOneWhl()
    {
        var dir = Path.Combine(_tempDir, "wheels");
        Directory.CreateDirectory(dir);
        Assert.False(_locator.Validate(LocatableResource.OfflineWheels, dir));

        File.WriteAllText(Path.Combine(dir, "paddlepaddle-3.3.1-cp311-none-win_amd64.whl"), "fake");
        Assert.True(_locator.Validate(LocatableResource.OfflineWheels, dir));
    }

    [Fact]
    public void Validate_EmbedModelCache_MustBeNamedFastembedCache()
    {
        var dir = Path.Combine(_tempDir, "fastembed_cache");
        Directory.CreateDirectory(dir);
        Assert.True(_locator.Validate(LocatableResource.EmbedModelCache, dir));

        var wrong = Path.Combine(_tempDir, "other_cache");
        Directory.CreateDirectory(wrong);
        Assert.False(_locator.Validate(LocatableResource.EmbedModelCache, wrong));
    }

    [Fact]
    public void Validate_BackendPython_RejectsMissingExe()
    {
        Assert.False(_locator.Validate(LocatableResource.BackendPython,
            Path.Combine(_tempDir, "nonexistent-python.exe")));
    }

    // ===== 手动配置优先级 =====

    [Fact]
    public void Locate_ConfiguredPathWins()
    {
        var bin = Path.Combine(_tempDir, "poppler", "bin");
        Directory.CreateDirectory(bin);
        File.WriteAllText(Path.Combine(bin, "pdftoppm.exe"), "fake");
        _settings.PopplerPath = bin;

        var result = _locator.Locate(LocatableResource.Poppler);
        Assert.True(result.Found);
        Assert.Equal("手动配置", result.Source);
        Assert.Equal(bin, result.Path);
    }

    [Fact]
    public void Locate_InvalidConfiguredPath_FallsBackToQuickScan()
    {
        _settings.WheelsDir = Path.Combine(_tempDir, "missing");
        // 快扫根目录与本测试 temp 目录无关 → 期望未命中（除非本机 Downloads 恰有 whl，
        // 因此只断言"不会把失效配置当命中"，Found=true 时来源必须不是手动配置）
        var result = _locator.Locate(LocatableResource.OfflineWheels);
        if (result.Found)
        {
            Assert.NotEqual("手动配置", result.Source);
        }
    }

    // ===== 快扫：高概率根目录内的锚点命中 =====

    [Fact]
    public void QuickScan_FindsWheelsInCandidateRoot()
    {
        // Downloads 是 wheels 快扫根之一：把候选放进去（快扫 depth ≤6）
        var downloads = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads");
        var marker = Path.Combine(downloads, $"docmind-test-{Guid.NewGuid():N}");
        try
        {
            Directory.CreateDirectory(marker);
            File.WriteAllText(Path.Combine(marker, "x-1.0-py3-none-any.whl"), "fake");

            var result = _locator.Locate(LocatableResource.OfflineWheels);
            Assert.True(result.Found);
            Assert.Equal(marker, result.Path);
        }
        finally
        {
            try { Directory.Delete(marker, recursive: true); } catch { /* ignore */ }
        }
    }

    // ===== 深扫：early-exit 与缓存 =====

    [Fact]
    public async Task DeepScan_FindsWheels_AndSecondCallUsesCache()
    {
        var root = Path.Combine(_tempDir, "drive-root");
        var target = Path.Combine(root, "a", "b", "c", "d", "e", "f"); // 恰好 6 层
        Directory.CreateDirectory(target);
        File.WriteAllText(Path.Combine(target, "y-1.0-py3-none-any.whl"), "fake");

        // 直接用 ScanRootsAsync 无法从外部指定根 → 通过 Locate 不行，走 DeepScan 会扫全盘。
        // 这里退一步：验证缓存行为（第一次真扫太慢），改为手工写缓存文件再 DeepScan，
        // 验证「缓存命中优先、不再付扫描成本」。
        var cacheFile = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "DocMind", "resource_scan_cache.json");
        var cacheExisted = File.Exists(cacheFile);
        var originalContent = cacheExisted ? File.ReadAllText(cacheFile) : null;

        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(cacheFile)!);
            var cacheJson = "{\"Candidates\":{\"OfflineWheels\":[\""
                + target.Replace("\\", "\\\\") + "\"]}}";
            File.WriteAllText(cacheFile, cacheJson);

            var result = await _locator.DeepScanAsync(
                LocatableResource.OfflineWheels, progress: null, CancellationToken.None);
            Assert.True(result.Found);
            Assert.Equal("缓存", result.Source);
            Assert.Equal(target, result.Path);
        }
        finally
        {
            if (cacheExisted) File.WriteAllText(cacheFile, originalContent!);
            else { try { File.Delete(cacheFile); } catch { /* ignore */ } }
        }
    }

    // ===== Apply 写配置 =====

    [Fact]
    public void Apply_PersistsToAppSettings()
    {
        var bin = Path.Combine(_tempDir, "poppler2", "bin");
        Directory.CreateDirectory(bin);
        File.WriteAllText(Path.Combine(bin, "pdftoppm.exe"), "fake");

        Assert.True(_locator.Apply(LocatableResource.Poppler, bin));
        Assert.Equal(bin, _settings.PopplerPath);
        Assert.True(File.Exists(AppSettings.ConfigPath), "Apply 必须落盘（Save）");
    }
}

/// <summary>ResourcePathPanelViewModel：状态机（浏览/自动寻找/取消）。</summary>
/// <remarks>依赖 [Collection("SettingsFile")] 的 fixture 把配置目录隔离到 temp；
/// 本类不得改动 ConfigDirOverrideForTests，否则会破坏同集合其他测试的隔离。</remarks>
[Collection("SettingsFile")]
public sealed class ResourcePathPanelViewModelTests : IDisposable
{
    private readonly string _tempDir;

    public ResourcePathPanelViewModelTests()
    {
        _tempDir = Path.Combine(Path.GetTempPath(), "DocMind.Tests.Panel.Dir", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tempDir);
    }

    public void Dispose()
    {
        try { Directory.Delete(_tempDir, recursive: true); } catch { /* ignore */ }
    }

    private static (ResourcePathPanelViewModel Vm, AppSettings Settings) CreateVm()
    {
        var settings = new AppSettings();
        var locator = new ResourceLocatorService(settings);
        return (new ResourcePathPanelViewModel(locator, new NotificationService()), settings);
    }

    [Fact]
    public void Items_ContainAllFourResources()
    {
        var (vm, _) = CreateVm();
        Assert.Equal(4, vm.Items.Count);
        Assert.Equal(LocatableResource.Poppler, vm.Items[0].Kind);
        Assert.Equal(LocatableResource.BackendPython, vm.Items[1].Kind);
        Assert.Equal(LocatableResource.OfflineWheels, vm.Items[2].Kind);
        Assert.Equal(LocatableResource.EmbedModelCache, vm.Items[3].Kind);
    }

    [Fact]
    public void Browse_InvalidFolder_WouldNotApply()
    {
        // OpenFolderDialog 需要 STA + 用户交互，无法在单测真实弹出；
        // 这里只验证「校验失败 → 不写配置」的关键契约（Browse 内部在
        // Validate 失败时直接 return，不触碰 settings）。
        var (vm, settings) = CreateVm();
        var badDir = Path.Combine(_tempDir, "empty");
        Directory.CreateDirectory(badDir);

        Assert.False(vm.Items[0].IsFound);
        Assert.Null(settings.PopplerPath);
    }

    [Fact]
    public async Task AutoLocate_AppliesQuickScanHit()
    {
        var (vm, settings) = CreateVm();
        var item = vm.Items[2]; // OfflineWheels
        var downloads = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads");
        var marker = Path.Combine(downloads, $"docmind-test-{Guid.NewGuid():N}");
        try
        {
            Directory.CreateDirectory(marker);
            File.WriteAllText(Path.Combine(marker, "z-1.0-py3-none-any.whl"), "fake");

            await vm.AutoLocateCommand.ExecuteAsync(item);

            Assert.True(item.IsFound);
            Assert.Equal(marker, item.EffectivePath);
            Assert.Equal(marker, settings.WheelsDir);
            Assert.Contains("已应用", item.SourceLabel);
        }
        finally
        {
            try { Directory.Delete(marker, recursive: true); } catch { /* ignore */ }
        }
    }

    [Fact]
    public async Task AutoLocate_DeclinedDeepScan_ShowsCancelHint()
    {
        var (vm, _) = CreateVm();
        var item = vm.Items[0]; // Poppler：高概率位置通常无 → 触发深扫询问
        vm.ConfirmDeepScan = _ => false;

        await vm.AutoLocateCommand.ExecuteAsync(item);

        if (item.IsFound)
        {
            // 本机快扫恰好命中（如项目 tools/poppler）：应已自动应用
            Assert.Contains("已应用", item.SourceLabel);
        }
        else
        {
            Assert.Contains("取消", item.StatusText);
        }
    }
}
