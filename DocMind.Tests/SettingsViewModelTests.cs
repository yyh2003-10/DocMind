using System.Text.Json;
using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using DocMind;

namespace DocMind.Tests;

/// <summary>把 AppSettings 落盘路径指到 temp 目录。
/// 此前 SaveAsync 直接写真实 %LOCALAPPDATA%\DocMind\appsettings.json，
/// 跑一次测试就会把用户已配置的 API Key 等真实配置覆盖掉。</summary>
public sealed class SettingsFileFixture : IDisposable
{
    public SettingsFileFixture()
    {
        AppSettings.ConfigDirOverrideForTests = Path.Combine(
            Path.GetTempPath(), "DocMind.Tests", Guid.NewGuid().ToString("N"));
    }

    public void Dispose()
    {
        try
        {
            var dir = AppSettings.ConfigDirOverrideForTests;
            if (dir is not null && Directory.Exists(dir))
            {
                Directory.Delete(dir, recursive: true);
            }
        }
        catch { /* temp 清理失败不影响测试结果 */ }
        AppSettings.ConfigDirOverrideForTests = null;
    }
}

[CollectionDefinition("SettingsFile")]
public sealed class SettingsFileCollection : ICollectionFixture<SettingsFileFixture>
{
}

/// <summary>
/// SettingsViewModel 单元测试：LLM 字段初始化、IsDirty 追踪、保存推送、Revert 恢复。
/// 落盘相关用例经 SettingsFileFixture 隔离到 temp 目录。
/// </summary>
[Collection("SettingsFile")]
public class SettingsViewModelTests
{
    private static SettingsViewModel CreateVm(
        AppSettings? appSettings = null,
        FakeDoc2kbApiService? fake = null)
    {
        appSettings ??= new AppSettings();
        var notifications = new NotificationService();
        var themeService = new ThemeService(appSettings, notifications);
fake ??= new FakeDoc2kbApiService();
    fake.OnUpdateConfig ??= (_, _) =>
        Task.FromResult(new BackendConfig { Notice = null });
    fake.OnGetGpuDiagnosis ??= _ =>
        Task.FromResult(new Models.GpuDiagnosis { RecommendedPath = "cpu" });
    fake.OnInstallGpu ??= (_, onLog, onDone, _) =>
    {
        onLog("[模拟] 安装完成");
        onDone(true);
        return Task.CompletedTask;
    };
    var backend = new BackendProcessService(appSettings);
    var gpuWarning = new GpuWarningViewModel(fake, backend, notifications);
    return new SettingsViewModel(appSettings, notifications, themeService, fake, gpuWarning, backend);
    }

    // ======================================================================
    // LLM 字段初始化
    // ======================================================================

    [Fact]
    public void Constructor_LoadsLlmFieldsFromAppSettings()
    {
        var settings = new AppSettings
        {
            LlmProvider = "openai",
            LlmApiKey = "sk-test-key",
            LlmBaseUrl = "https://api.deepseek.com/v1",
            LlmModel = "deepseek-chat",
            LlmTemperature = 0.3,
            LlmMaxTokens = 1024,
            RagTopK = 8,
        };

        var vm = CreateVm(settings);

        Assert.Equal("openai", vm.LlmProvider);
        Assert.Equal("sk-test-key", vm.LlmApiKey);
        Assert.Equal("https://api.deepseek.com/v1", vm.LlmBaseUrl);
        Assert.Equal("deepseek-chat", vm.LlmModel);
        Assert.Equal(0.3, vm.LlmTemperature);
        Assert.Equal(1024, vm.LlmMaxTokens);
        Assert.Equal(8, vm.RagTopK);
        Assert.False(vm.IsDirty); // 加载后不应 dirty
    }

    [Fact]
    public void Constructor_UsesDefaultsFromAppSettings()
    {
        var settings = new AppSettings(); // 默认值
        var vm = CreateVm(settings);

        Assert.Equal("none", vm.LlmProvider);
        Assert.Null(vm.LlmApiKey);
        Assert.Null(vm.LlmBaseUrl);
        Assert.Equal("", vm.LlmModel);
        Assert.Equal(0.7, vm.LlmTemperature);
        Assert.Equal(2048, vm.LlmMaxTokens);
        Assert.Equal(5, vm.RagTopK);
    }

    // ======================================================================
    // IsDirty 追踪
    // ======================================================================

    [Fact]
    public void SettingLlmField_MarksIsDirty()
    {
        var vm = CreateVm();
        Assert.False(vm.IsDirty);

        vm.LlmProvider = "openai";
        Assert.True(vm.IsDirty);
    }

    [Fact]
    public void SettingLlmProvider_ToSameValue_DoesNotMarkDirty()
    {
        var vm = CreateVm();
        Assert.False(vm.IsDirty);

        vm.LlmProvider = "none"; // 已经是默认值
        Assert.False(vm.IsDirty);
    }

    [Fact]
    public void SettingLlmApiKey_MarksIsDirty()
    {
        var vm = CreateVm();
        vm.LlmApiKey = "sk-new-key";
        Assert.True(vm.IsDirty);
    }

    // ======================================================================
    // SaveCommand 可用性
    // ======================================================================

    [Fact]
    public void SaveCommand_Disabled_WhenNotDirty()
    {
        var vm = CreateVm();
        Assert.False(vm.SaveCommand.CanExecute(null));
    }

    [Fact]
    public void SaveCommand_Enabled_WhenDirty()
    {
        var vm = CreateVm();
        vm.LlmProvider = "ollama";
        Assert.True(vm.SaveCommand.CanExecute(null));
    }

    // ======================================================================
    // SaveAsync 推送 LLM 配置到后端
    // ======================================================================

    [Fact]
    public async Task SaveAsync_PushesLlmFieldsToBackend()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var settings = new AppSettings();
        var vm = CreateVm(settings, fake);

        // 修改 LLM 字段
        vm.LlmProvider = "openai";
        vm.LlmApiKey = "sk-push-test";
        vm.LlmBaseUrl = "https://api.deepseek.com/v1";
        vm.LlmModel = "deepseek-chat";
        vm.LlmTemperature = 0.5;
        vm.LlmMaxTokens = 4096;
        vm.RagTopK = 10;

        // 确认 dirty 状态
        Assert.True(vm.IsDirty);
        Assert.True(vm.SaveCommand.CanExecute(null));

        // 执行保存，捕获异常以便调试
        try
        {
            await vm.SaveCommand.ExecuteAsync(null);
        }
        catch (Exception ex)
        {
            Assert.Fail($"SaveAsync threw: {ex}");
        }

        // 验证 API 被调用
        if (captured is null)
        {
            Assert.Fail($"OnUpdateConfig 未被调用。StatusMessage: '{vm.StatusMessage}', IsDirty: {vm.IsDirty}");
        }
        Assert.Equal("openai", captured.LlmProvider);
        Assert.Equal("openai", captured.LlmProvider);
        Assert.Equal("sk-push-test", captured.LlmApiKey);
        Assert.Equal("https://api.deepseek.com/v1", captured.LlmBaseUrl);
        Assert.Equal("deepseek-chat", captured.LlmModel);
        Assert.Equal(0.5, captured.LlmTemperature);
        Assert.Equal(4096, captured.LlmMaxTokens);
        Assert.Equal(10, captured.RagTopK);
        // 保存后 IsDirty 应重置
        Assert.False(vm.IsDirty);
    }

    [Fact]
    public async Task SaveAsync_WritesToAppSettings()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (_, _) =>
            Task.FromResult(new BackendConfig { Notice = null });

        var settings = new AppSettings();
        var vm = CreateVm(settings, fake);

        vm.LlmProvider = "ollama";
        vm.LlmModel = "llama3.2";
        vm.LlmTemperature = 0.8;

        await vm.SaveCommand.ExecuteAsync(null);

        // AppSettings 内存对象已更新
        Assert.Equal("ollama", settings.LlmProvider);
        Assert.Equal("llama3.2", settings.LlmModel);
        Assert.Equal(0.8, settings.LlmTemperature);
    }

    [Fact]
    public async Task SaveAsync_BackendFailure_DoesNotThrow()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (_, _) => throw new InvalidOperationException("后端不可达");

        var vm = CreateVm(new AppSettings(), fake);
        vm.LlmProvider = "openai";

        // 后端推送失败不应阻断保存流程
        var ex = await Record.ExceptionAsync(() => vm.SaveCommand.ExecuteAsync(null));
        Assert.Null(ex);
        // IsDirty 应重置（本地保存成功）
        Assert.False(vm.IsDirty);
    }

    // ======================================================================
    // Revert
    // ======================================================================

    [Fact]
    public void Revert_RestoresLlmFieldsFromAppSettings()
    {
        var settings = new AppSettings
        {
            LlmProvider = "openai",
            LlmApiKey = "sk-original",
            LlmModel = "deepseek-chat",
        };
        var vm = CreateVm(settings);

        // 修改后
        vm.LlmProvider = "ollama";
        vm.LlmApiKey = "sk-modified";
        vm.LlmModel = "llama3.2";
        Assert.True(vm.IsDirty);

        // Revert
        vm.RevertCommand.Execute(null);

        Assert.Equal("openai", vm.LlmProvider);
        Assert.Equal("sk-original", vm.LlmApiKey);
        Assert.Equal("deepseek-chat", vm.LlmModel);
        Assert.False(vm.IsDirty);
    }

    // ======================================================================
    // 分块字段也标记 dirty
    // ======================================================================

    [Fact]
    public void SettingChunkField_MarksIsDirty()
    {
        var vm = CreateVm();
        vm.ChunkMaxTokens = 2000;
        Assert.True(vm.IsDirty);
    }

    [Fact]
    public void SettingEmbedModel_MarksIsDirty()
    {
        var vm = CreateVm();
        vm.EmbedModel = "BAAI/bge-base-en-v1.5";
        Assert.True(vm.IsDirty);
    }

    [Fact]
    public void SupportedEmbedModels_ContainsExpected()
    {
        var vm = CreateVm();
        Assert.Contains("BAAI/bge-small-zh-v1.5", vm.SupportedEmbedModels);
        Assert.Contains("BAAI/bge-base-en-v1.5", vm.SupportedEmbedModels);
    }

    // ======================================================================
    // 测试连接
    // ======================================================================

    [Fact]
    public async Task TestConnection_BackendHealthFails_ShowsError()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetHealth = _ => throw new InvalidOperationException("后端不可达");
        var vm = CreateVm(new AppSettings(), fake);

        await vm.TestConnectionCommand.ExecuteAsync(null);

        Assert.Contains("连接失败", vm.StatusMessage);
        Assert.False(vm.IsTestingConnection);
    }

    [Fact]
    public async Task TestConnection_NoLlmConfigured_ShowsBackendOk()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetHealth = _ => Task.FromResult(new HealthStatus { Status = "ok" });
        var vm = CreateVm(new AppSettings(), fake);

        await vm.TestConnectionCommand.ExecuteAsync(null);

        // AppSettings 默认 LlmProvider=none → 只测后端，不调 LLM 测试
        Assert.Contains("后端连接正常", vm.StatusMessage);
        Assert.False(vm.IsTestingConnection);
    }

    [Fact]
    public async Task TestConnection_LlmConfigured_CallsLlmTestWithUiValues()
    {
        LlmTestRequest? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnGetHealth = _ => Task.FromResult(new HealthStatus { Status = "ok" });
        fake.OnLlmTest = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new LlmTestResult
            {
                Ok = true,
                Provider = "openai",
                Model = "deepseek-chat",
                ReplyPreview = "pong",
                ElapsedMs = 42,
            });
        };
        var vm = CreateVm(new AppSettings(), fake);
        // 用户刚输入、尚未保存的值也应被测试到
        vm.LlmProvider = "openai";
        vm.LlmApiKey = "sk-fresh";
        vm.LlmBaseUrl = "https://api.deepseek.com/v1";
        vm.LlmModel = "deepseek-chat";

        await vm.TestConnectionCommand.ExecuteAsync(null);

        Assert.Contains("LLM 连接成功", vm.StatusMessage);
        Assert.Contains("deepseek-chat", vm.StatusMessage);
        Assert.NotNull(captured);
        Assert.Equal("openai", captured.Provider);
        Assert.Equal("sk-fresh", captured.ApiKey);
        Assert.Equal("https://api.deepseek.com/v1", captured.BaseUrl);
        Assert.Equal("deepseek-chat", captured.Model);
        Assert.False(vm.IsTestingConnection);
    }

    [Fact]
    public async Task TestConnection_LlmTestReturnsFailure_ShowsClassifiedError()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetHealth = _ => Task.FromResult(new HealthStatus { Status = "ok" });
        fake.OnLlmTest = (_, _) => Task.FromResult(new LlmTestResult
        {
            Ok = false,
            Provider = "anthropic",
            Error = "Anthropic API API Key 无效 (HTTP 401): bad key",
        });
        var vm = CreateVm(new AppSettings(), fake);
        vm.LlmProvider = "anthropic";

        await vm.TestConnectionCommand.ExecuteAsync(null);

        Assert.Contains("LLM 测试失败", vm.StatusMessage);
        Assert.Contains("API Key 无效", vm.StatusMessage);
        Assert.False(vm.IsTestingConnection);
    }

    [Fact]
    public async Task TestConnection_LlmTestThrows_ShowsConnectionError()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetHealth = _ => Task.FromResult(new HealthStatus { Status = "ok" });
        fake.OnLlmTest = (_, _) => throw new ApiException("AUTH_ERROR", "API Key 无效");
        var vm = CreateVm(new AppSettings(), fake);
        vm.LlmProvider = "openai";

        await vm.TestConnectionCommand.ExecuteAsync(null);

        Assert.Contains("连接失败", vm.StatusMessage);
        Assert.Contains("API Key 无效", vm.StatusMessage);
        Assert.False(vm.IsTestingConnection);
    }

    [Fact]
    public async Task TestConnection_SetsIsTestingDuringAndFalseAfter()
    {
        var tcs = new TaskCompletionSource<HealthStatus>();
        var fake = new FakeDoc2kbApiService();
        fake.OnGetHealth = _ => tcs.Task;
        var vm = CreateVm(new AppSettings(), fake);

        var task = vm.TestConnectionCommand.ExecuteAsync(null);

        Assert.True(vm.IsTestingConnection);

        tcs.SetResult(new HealthStatus { Status = "ok" });
        await task;

        Assert.False(vm.IsTestingConnection);
    }

    // ======================================================================
    // API Key 保存语义（留空 = 保留原值；「清除」按钮 = 显式删除）
    // ======================================================================

    [Fact]
    public async Task SaveAsync_EmptyApiKey_KeepsExistingKeyAndSkipsPush()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var settings = new AppSettings { LlmProvider = "openai", LlmApiKey = "sk-orig" };
        var vm = CreateVm(settings, fake);
        Assert.True(vm.HasSavedApiKey);

        vm.LlmApiKey = "";            // 用户清空输入框（留空 ≠ 清除）
        vm.LlmModel = "gpt-4o-mini";  // 顺手改其他字段
        await vm.SaveCommand.ExecuteAsync(null);

        Assert.Null(captured!.LlmApiKey);            // 后端不修改（null）
        Assert.Equal("sk-orig", settings.LlmApiKey); // 本地保留原值
        Assert.Contains("保留原值", vm.StatusMessage);
    }

    [Fact]
    public async Task SaveAsync_ClearApiKeyCommand_ClearsLocallyAndBackend()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var settings = new AppSettings { LlmProvider = "openai", LlmApiKey = "sk-orig" };
        var vm = CreateVm(settings, fake);

        vm.ClearApiKeyCommand.Execute(null);
        Assert.Null(vm.LlmApiKey);
        Assert.True(vm.IsDirty); // 清除请求本身标记 dirty，保存按钮可用

        await vm.SaveCommand.ExecuteAsync(null);

        Assert.Equal("", captured!.LlmApiKey);   // 后端显式清除（空串）
        Assert.Null(settings.LlmApiKey);         // 本地置空
        Assert.False(vm.HasSavedApiKey);
    }

    [Fact]
    public async Task SaveAsync_ReInputAfterClear_CancelsClearRequest()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var settings = new AppSettings { LlmProvider = "openai", LlmApiKey = "sk-orig" };
        var vm = CreateVm(settings, fake);

        vm.ClearApiKeyCommand.Execute(null);
        vm.LlmApiKey = "sk-new"; // 清除后又重新输入 → 取消清除请求
        await vm.SaveCommand.ExecuteAsync(null);

        Assert.Equal("sk-new", captured!.LlmApiKey);
        Assert.Equal("sk-new", settings.LlmApiKey);
    }

    [Fact]
    public async Task Revert_AfterClearApiKey_RestoresKeyAndCancelsClear()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var settings = new AppSettings { LlmProvider = "openai", LlmApiKey = "sk-orig" };
        var vm = CreateVm(settings, fake);

        vm.ClearApiKeyCommand.Execute(null);
        vm.RevertCommand.Execute(null);
        Assert.Equal("sk-orig", vm.LlmApiKey);

        vm.LlmModel = "deepseek-chat"; // 随便改一个字段触发保存
        await vm.SaveCommand.ExecuteAsync(null);
        // 清除请求已随 Revert 取消：推送恢复后的原值（幂等），而不是空串清除
        Assert.Equal("sk-orig", captured!.LlmApiKey);
    }

    [Fact]
    public void Constructor_DecryptFailedFlag_ShowsWarning()
    {
        var settings = new AppSettings { LlmProvider = "openai", LlmKeyDecryptFailed = true };
        var vm = CreateVm(settings);

        Assert.Null(vm.LlmApiKey); // 密文解密失败按未配置处理
        Assert.Contains("无法解密", vm.StatusMessage);
    }

    // ======================================================================
    // AppSettings.Save() 唯一落盘出口：密文落盘 + 全字段 + 内存明文
    // ======================================================================

    [Fact]
    public void AppSettings_Save_WritesEncryptedKeyAndKeepsPlaintextInMemory()
    {
        var settings = new AppSettings { LlmApiKey = "sk-plain", RequestTimeoutSec = 120 };

        settings.Save();

        var json = File.ReadAllText(AppSettings.ConfigPath);
        Assert.Contains("\"llmApiKey\": \"dpapi:v1:", json); // 落盘是 DPAPI 密文
        Assert.Contains("\"requestTimeoutSec\": 120", json); // 全字段（此前匿名对象会丢此字段）
        Assert.Equal("sk-plain", settings.LlmApiKey);        // 单例仍持明文（不被 Save 改动）

        // 回读后可解密还原
        var reloaded = JsonSerializer.Deserialize<AppSettings>(json,
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
        Assert.NotNull(reloaded);
        Assert.Equal("sk-plain", SecretProtector.Unprotect(reloaded!.LlmApiKey));
    }

    [Fact]
    public async Task SaveAsync_WritesToIsolatedTempConfig_NotRealUserConfig()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (_, _) => Task.FromResult(new BackendConfig { Notice = null });

        var settings = new AppSettings();
        var vm = CreateVm(settings, fake);
        vm.LlmTemperature = 0.9;
        await vm.SaveCommand.ExecuteAsync(null);

        // 落盘发生在 temp 覆写目录下，而不是真实 %LOCALAPPDATA%\DocMind
        Assert.StartsWith(Path.GetTempPath(), AppSettings.ConfigPath, StringComparison.OrdinalIgnoreCase);
        Assert.True(File.Exists(AppSettings.ConfigPath));
    }

    // ======================================================================
    // 后端配置回填（/v1/config）：key 已配置态 / config.toml 损坏告警
    // ======================================================================

    [Fact]
    public async Task LoadBackendConfig_BackendKeyConfigured_EnablesClear()
    {
        // 本地 appsettings 无 key，但后端报告已配置（环境变量等注入）→ 清除按钮应可用
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnGetConfig = _ =>
            Task.FromResult(new BackendConfig { LlmApiKeyConfigured = true, Notice = null });
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var settings = new AppSettings { LlmProvider = "openai" }; // 本地无 key
        var vm = CreateVm(settings, fake);

        // 构造时 fire-and-forget 拉取尚未完成，等待后应回填
        await vm.LoadBackendConfigAsync();
        Assert.True(vm.HasSavedApiKey);

        // 此时「清除」应真正推到后端（推 "" 而不是 null = 不修改）
        vm.ClearApiKeyCommand.Execute(null);
        Assert.True(vm.IsDirty);
        await vm.SaveCommand.ExecuteAsync(null);
        Assert.Equal("", captured!.LlmApiKey);
    }

    [Fact]
    public async Task LoadBackendConfig_ConfigError_ShowsWarning()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetConfig = _ =>
            Task.FromResult(new BackendConfig
            {
                LlmApiKeyConfigured = false,
                ConfigError = "config.toml 解析失败（损坏）",
                Notice = null,
            });

        var vm = CreateVm(new AppSettings(), fake);
        await vm.LoadBackendConfigAsync();

        Assert.Contains("config.toml", vm.StatusMessage);
    }

    [Fact]
    public async Task LoadBackendConfig_Unreachable_StaysSilent()
    {
        // 后端不可达/未实现：不抛异常、状态保持就绪
        var fake = new FakeDoc2kbApiService(); // OnGetConfig 默认抛 NotImplementedException
        var vm = CreateVm(new AppSettings(), fake);

        await vm.LoadBackendConfigAsync(); // 不应抛出

        Assert.Equal("就绪", vm.StatusMessage);
        Assert.False(vm.HasSavedApiKey);
    }

    // ======================================================================
    // 模型名清除语义（曾配置过+现清空 → 推 "" 显式清除）
    // ======================================================================

    [Fact]
    public async Task SaveAsync_ClearsModel_WhenPreviouslyConfigured()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var settings = new AppSettings { LlmProvider = "openai", LlmModel = "deepseek-chat" };
        var vm = CreateVm(settings, fake);

        // 清空模型名 → 曾配置过，应推 "" 显式清除后端的旧模型
        vm.LlmModel = "";
        vm.LlmTemperature = 0.6; // 顺手改一个字段触发保存
        await vm.SaveCommand.ExecuteAsync(null);

        Assert.Equal("", captured!.LlmModel);
    }

    [Fact]
    public async Task SaveAsync_ModelNeverConfigured_DoesNotPushClear()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var settings = new AppSettings { LlmProvider = "openai" }; // 从未配置模型
        var vm = CreateVm(settings, fake);

        // 清空模型名（本来就空）→ 推 null（不修改后端，避免误清后端手动配置）
        vm.LlmTemperature = 0.6;
        await vm.SaveCommand.ExecuteAsync(null);

        Assert.Null(captured!.LlmModel);
    }

    // ======================================================================
    // 获取模型列表（POST /v1/llm/models）
    // ======================================================================

    [Fact]
    public async Task RefreshLlmModels_Success_FillsCandidateList()
    {
        LlmModelsRequest? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnLlmModels = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new LlmModelsResult
            {
                Ok = true,
                Provider = "ollama",
                Models = new[] { "llama3.2:latest", "qwen2.5:7b", "deepseek-r1:8b" },
            });
        };

        var settings = new AppSettings { LlmProvider = "ollama", LlmModel = "llama3.2" };
        var vm = CreateVm(settings, fake);

        await vm.RefreshLlmModelsCommand.ExecuteAsync(null);

        Assert.Equal(3, vm.LlmModels.Count);
        Assert.Contains("qwen2.5:7b", vm.LlmModels);
        // 请求带 UI 当前输入值（未保存也能拉）
        Assert.Equal("ollama", captured!.Provider);
        Assert.Contains("3 个模型", vm.StatusMessage);
    }

    [Fact]
    public async Task RefreshLlmModels_NoneProvider_ShowsHint()
    {
        var fake = new FakeDoc2kbApiService();
        var vm = CreateVm(new AppSettings { LlmProvider = "none" }, fake);

        await vm.RefreshLlmModelsCommand.ExecuteAsync(null);

        Assert.Empty(vm.LlmModels);
        Assert.Contains("请先选择 LLM 提供商", vm.StatusMessage);
    }

    [Fact]
    public async Task RefreshLlmModels_BackendError_ShowsClassifiedError()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnLlmModels = (_, _) => Task.FromResult(new LlmModelsResult
        {
            Ok = false,
            Provider = "openai",
            Error = "OpenAI API API Key 无效 (HTTP 401): ...",
        });

        var vm = CreateVm(new AppSettings { LlmProvider = "openai" }, fake);
        await vm.RefreshLlmModelsCommand.ExecuteAsync(null);

        Assert.Empty(vm.LlmModels);
        Assert.Contains("401", vm.StatusMessage);
    }

    // ======================================================================
    // 系统提示词（RagSystemPrompt）
    // ======================================================================

    [Fact]
    public void Constructor_LoadsRagSystemPromptFromAppSettings()
    {
        var settings = new AppSettings { RagSystemPrompt = "用文言文回答。" };
        var vm = CreateVm(settings);

        Assert.Equal("用文言文回答。", vm.RagSystemPrompt);
        Assert.False(vm.IsDirty);
    }

    [Fact]
    public async Task SaveAsync_PushesRagSystemPrompt()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };
        var settings = new AppSettings();
        var vm = CreateVm(settings, fake);

        vm.RagSystemPrompt = "你是一个严谨的法律文档助手。";
        await vm.SaveCommand.ExecuteAsync(null);

        Assert.Equal("你是一个严谨的法律文档助手。", captured!.RagSystemPrompt);
        Assert.Equal("你是一个严谨的法律文档助手。", settings.RagSystemPrompt); // 本地 AppSettings 同步
    }

    [Fact]
    public async Task SaveAsync_WasConfiguredNowEmpty_PushesExplicitClear()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };
        // 曾配置过系统提示词
        var settings = new AppSettings { RagSystemPrompt = "旧提示词", LlmProvider = "openai" };
        var vm = CreateVm(settings, fake);

        vm.RagSystemPrompt = ""; // 清空保存 → 显式清除（推 ""）
        await vm.SaveCommand.ExecuteAsync(null);

        Assert.Equal("", captured!.RagSystemPrompt);
        Assert.Null(settings.RagSystemPrompt); // 本地置空
    }

    [Fact]
    public async Task SaveAsync_NeverConfiguredEmpty_PushesNull()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };
        var settings = new AppSettings(); // 从未配置
        var vm = CreateVm(settings, fake);

        vm.LlmTemperature = 0.5; // 触发 dirty，系统提示词保持空
        await vm.SaveCommand.ExecuteAsync(null);

        Assert.Null(captured!.RagSystemPrompt);
    }

    [Fact]
    public async Task WatchPaths_AddRemoveAndSave_PushesToBackend()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var settings = new AppSettings { WatchPaths = new List<string> { "C:/docs" } };
        var vm = CreateVm(settings, fake);

        Assert.Single(vm.WatchPaths);
        Assert.Equal("C:/docs", vm.WatchPaths[0]);

        // 添加新路径
        vm.NewWatchPath = "D:/notes";
        vm.AddWatchPathCommand.Execute(null);
        Assert.Equal(2, vm.WatchPaths.Count);
        Assert.Empty(vm.NewWatchPath);

        // 重复添加忽略
        vm.NewWatchPath = "D:/notes";
        vm.AddWatchPathCommand.Execute(null);
        Assert.Equal(2, vm.WatchPaths.Count);

        // 移除路径
        vm.RemoveWatchPathCommand.Execute("C:/docs");
        Assert.Single(vm.WatchPaths);
        Assert.Equal("D:/notes", vm.WatchPaths[0]);

        // 保存
        await vm.SaveCommand.ExecuteAsync(null);
        Assert.NotNull(captured);
        Assert.Single(captured.WatchPaths!);
        Assert.Equal("D:/notes", captured.WatchPaths![0]);
        Assert.Single(settings.WatchPaths);
        Assert.Equal("D:/notes", settings.WatchPaths[0]);
    }
}