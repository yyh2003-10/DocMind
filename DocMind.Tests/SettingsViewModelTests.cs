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
        Assert.Equal(8192, vm.LlmMaxTokens);
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
    public void EmbedModelOptions_ContainsExpected()
    {
        var vm = CreateVm();
        Assert.Contains(vm.EmbedModelOptions, o => o.ModelId == "BAAI/bge-small-zh-v1.5");
        Assert.Contains(vm.EmbedModelOptions, o => o.ModelId == "BAAI/bge-base-en-v1.5");
    }

    [Fact]
    public void EmbedModelOptions_CustomModelOutsideCatalog_AddedAsOption()
    {
        var settings = new AppSettings { EmbedModel = "my-org/custom-model" };
        var vm = CreateVm(settings);
        // 不在推荐清单的模型值应补一个自定义项，避免下拉选中态空白
        Assert.Contains(vm.EmbedModelOptions, o => o.ModelId == "my-org/custom-model");
        Assert.Equal("my-org/custom-model", vm.EmbedModel);
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
        Assert.Contains("qwen2.5:7b", vm.LlmModels.Select(i => i.Name));
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

    // ======================================================================
    // 预设模版与系统全面体检 (Doctor) 测试
    // ======================================================================

    [Fact]
    public void SelectedPreset_AppliesDeepSeekTemplate()
    {
        var vm = CreateVm(new AppSettings());
        var deepseekPreset = vm.AvailablePresets.First(p => p.Id == "deepseek");

        vm.SelectedPreset = deepseekPreset;

        Assert.Equal("openai", vm.LlmProvider);
        Assert.Equal("https://api.deepseek.com/v1", vm.LlmBaseUrl);
        Assert.Equal("deepseek-chat", vm.LlmModel);
        Assert.Contains("deepseek-chat", vm.LlmModels.Select(i => i.Name));
        Assert.Contains("DeepSeek", vm.StatusMessage);
    }

    [Fact]
    public void SelectedPreset_AppliesOllamaTemplate()
    {
        var vm = CreateVm(new AppSettings());
        var ollamaPreset = vm.AvailablePresets.First(p => p.Id == "ollama");

        vm.SelectedPreset = ollamaPreset;

        Assert.Equal("ollama", vm.LlmProvider);
        Assert.Equal("http://localhost:11434", vm.LlmBaseUrl);
        Assert.Equal("qwen2.5:7b", vm.LlmModel);
        Assert.Contains("qwen2.5:7b", vm.LlmModels.Select(i => i.Name));
    }

    [Fact]
    public async Task RunDoctorCommand_FetchesReportAndUpdatesScore()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnGetDoctorReport = (net, ct) => Task.FromResult(new DoctorReportResult
            {
                OverallStatus = "ok",
                Score = 95,
                Summary = "系统运行状态良好",
                Checks = new List<DiagnosticCheckItem>
                {
                    new() { Name = "Python 运行环境", Category = "python", Status = "ok", Message = "Python 3.11.0" },
                    new() { Name = "本地存储读写", Category = "storage", Status = "ok", Message = "正常" },
                }
            })
        };

        var vm = CreateVm(new AppSettings(), fake);
        Assert.False(vm.HasDoctorReport);

        await vm.RunDoctorCommand.ExecuteAsync(null);

        Assert.True(vm.HasDoctorReport);
        Assert.Equal(95, vm.DoctorReport!.Score);
        Assert.Equal("95 / 100", vm.DoctorScoreText);
        Assert.Equal(2, vm.DoctorReport.Checks.Count);
        Assert.Contains("95", vm.StatusMessage);
    }

    // ======================================================================
    // AI 提供商档案：保存/应用/删除/兼容
    // ======================================================================

    [Fact]
    public void Constructor_LoadsSavedProfiles_AndSelectsActive()
    {
        var active = new LlmProfile { Id = "p1", Name = "DeepSeek", Provider = "openai" };
        var other = new LlmProfile { Id = "p2", Name = "Ollama 本地", Provider = "ollama" };
        var settings = new AppSettings
        {
            LlmProfiles = new List<LlmProfile> { active, other },
            ActiveProfileId = "p1",
        };

        var vm = CreateVm(settings);

        Assert.Equal(2, vm.SavedProfiles.Count);
        Assert.Equal("p1", vm.SelectedProfile?.Id);
        Assert.True(vm.HasSavedProfiles);
    }

    [Fact]
    public void Constructor_NoProfiles_EmptyState()
    {
        var vm = CreateVm(new AppSettings());
        Assert.Empty(vm.SavedProfiles);
        Assert.False(vm.HasSavedProfiles);
        Assert.Null(vm.SelectedProfile);
    }

    [Fact]
    public void SaveProfile_CreatesNewProfile_WithFormFields()
    {
        var settings = new AppSettings();
        var vm = CreateVm(settings);

        vm.LlmProvider = "openai";
        vm.LlmApiKey = "sk-profile-key";
        vm.LlmBaseUrl = "https://api.deepseek.com/v1";
        vm.LlmModel = "deepseek-chat";
        vm.LlmTemperature = 0.3;
        vm.LlmMaxTokens = 1024;
        vm.ProfileNameInput = "工作用 DeepSeek";

        vm.SaveProfileCommand.Execute(null);

        var profile = Assert.Single(vm.SavedProfiles);
        Assert.Equal("工作用 DeepSeek", profile.Name);
        Assert.Equal("openai", profile.Provider);
        Assert.Equal("sk-profile-key", profile.ApiKey);
        Assert.Equal("https://api.deepseek.com/v1", profile.BaseUrl);
        Assert.Equal("deepseek-chat", profile.Model);
        Assert.Equal(0.3, profile.Temperature);
        Assert.Equal(1024, profile.MaxTokens);
        // 已同步回 AppSettings（供落盘）
        Assert.Single(settings.LlmProfiles);
        Assert.Equal("工作用 DeepSeek", settings.LlmProfiles[0].Name);
        // 保存后名称输入清空，选中新档案
        Assert.Equal("", vm.ProfileNameInput);
        Assert.Equal("工作用 DeepSeek", vm.SelectedProfile?.Name);
    }

    [Fact]
    public void SaveProfile_SameName_OverwritesExisting()
    {
        var existing = new LlmProfile { Id = "p1", Name = "DeepSeek", Provider = "openai", Model = "deepseek-chat" };
        var settings = new AppSettings { LlmProfiles = new List<LlmProfile> { existing } };
        var vm = CreateVm(settings);

        vm.LlmProvider = "openai";
        vm.LlmModel = "deepseek-reasoner";
        vm.ProfileNameInput = "DeepSeek"; // 同名 → 覆盖更新

        vm.SaveProfileCommand.Execute(null);

        Assert.Single(vm.SavedProfiles);
        var updated = vm.SavedProfiles[0];
        Assert.Equal("p1", updated.Id); // Id 保持不变
        Assert.Equal("deepseek-reasoner", updated.Model);
    }

    [Fact]
    public void SaveProfile_NoName_NoSelection_ShowsWarning()
    {
        var vm = CreateVm(new AppSettings());
        vm.LlmProvider = "openai";

        vm.SaveProfileCommand.Execute(null);

        Assert.Empty(vm.SavedProfiles);
        Assert.Contains("名称", vm.StatusMessage);
    }

    [Fact]
    public void SaveProfile_ProviderNone_Rejected()
    {
        var vm = CreateVm(new AppSettings());
        vm.ProfileNameInput = "未选提供商";

        vm.SaveProfileCommand.Execute(null);

        Assert.Empty(vm.SavedProfiles);
        Assert.Contains("接口类型", vm.StatusMessage);
    }

    [Fact]
    public async Task ApplyProfile_FillsForm_AndPushesToBackend()
    {
        BackendConfigUpdate? captured = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (req, _) =>
        {
            captured = req;
            return Task.FromResult(new BackendConfig { Notice = null });
        };

        var profile = new LlmProfile
        {
            Id = "p1",
            Name = "硅基流动",
            Provider = "openai",
            BaseUrl = "https://api.siliconflow.cn/v1",
            Model = "deepseek-ai/DeepSeek-V3",
            ApiKey = "sk-profile-2",
            Temperature = 0.5,
            MaxTokens = 4096,
        };
        var settings = new AppSettings { LlmProfiles = new List<LlmProfile> { profile } };
        var vm = CreateVm(settings, fake);
        vm.SelectedProfile = vm.SavedProfiles[0];

        await vm.ApplyProfileCommand.ExecuteAsync(null);

        // 表单回填
        Assert.Equal("openai", vm.LlmProvider);
        Assert.Equal("https://api.siliconflow.cn/v1", vm.LlmBaseUrl);
        Assert.Equal("deepseek-ai/DeepSeek-V3", vm.LlmModel);
        Assert.Equal("sk-profile-2", vm.LlmApiKey);
        Assert.Equal(0.5, vm.LlmTemperature);
        Assert.Equal(4096, vm.LlmMaxTokens);
        // 推送 payload 断言（含 key 与温度/token 覆盖）
        Assert.NotNull(captured);
        Assert.Equal("openai", captured.LlmProvider);
        Assert.Equal("sk-profile-2", captured.LlmApiKey);
        Assert.Equal("https://api.siliconflow.cn/v1", captured.LlmBaseUrl);
        Assert.Equal("deepseek-ai/DeepSeek-V3", captured.LlmModel);
        Assert.Equal(0.5, captured.LlmTemperature);
        Assert.Equal(4096, captured.LlmMaxTokens);
        // 激活档案已记录
        Assert.Equal("p1", settings.ActiveProfileId);
        // 保存后 dirty 重置
        Assert.False(vm.IsDirty);
    }

    [Fact]
    public void DeleteProfile_RemovesAndClearsActive()
    {
        var active = new LlmProfile { Id = "p1", Name = "DeepSeek", Provider = "openai" };
        var settings = new AppSettings
        {
            LlmProfiles = new List<LlmProfile> { active },
            ActiveProfileId = "p1",
        };
        var vm = CreateVm(settings);
        vm.DeleteConfirm = _ => true; // 测试绕过确认对话框
        vm.SelectedProfile = vm.SavedProfiles[0];

        vm.DeleteProfileCommand.Execute(null);

        Assert.Empty(vm.SavedProfiles);
        Assert.False(vm.HasSavedProfiles);
        Assert.Null(vm.SelectedProfile);
        Assert.Null(settings.ActiveProfileId);
        Assert.Empty(settings.LlmProfiles);
    }

    [Fact]
    public void DeleteProfile_Declined_KeepsProfile()
    {
        var profile = new LlmProfile { Id = "p1", Name = "DeepSeek", Provider = "openai" };
        var settings = new AppSettings { LlmProfiles = new List<LlmProfile> { profile } };
        var vm = CreateVm(settings);
        vm.DeleteConfirm = _ => false; // 用户取消
        vm.SelectedProfile = vm.SavedProfiles[0];

        vm.DeleteProfileCommand.Execute(null);

        Assert.Single(vm.SavedProfiles);
        Assert.Single(settings.LlmProfiles);
    }

    [Fact]
    public async Task SaveProfile_ApiKeyPersistsEncrypted_AndReloadsPlaintext()
    {
        // 落盘隔离到 temp 目录（SettingsFileFixture 已保证）
        var settings = new AppSettings();
        var vm = CreateVm(settings);
        vm.LlmProvider = "openai";
        vm.LlmApiKey = "sk-secret-encrypt";
        vm.LlmModel = "deepseek-chat";
        vm.ProfileNameInput = "加密档案";

        vm.SaveProfileCommand.Execute(null);
        vm.SaveCommand.Execute(null); // 触发 AppSettings.Save() 落盘

        // 内存仍持明文（运行态语义）
        Assert.Equal("sk-secret-encrypt", settings.LlmProfiles[0].ApiKey);

        // 磁盘上的 appsettings.json 中 ApiKey 应为 DPAPI 密文（非明文）
        var onDisk = System.IO.File.ReadAllText(AppSettings.ConfigPath);
        Assert.Contains("dpapi:v1:", onDisk);
        Assert.DoesNotContain("sk-secret-encrypt", onDisk);

        // 模拟 App.LoadSettings 载入路径：反序列化后对密文 Unprotect 还原明文
        var reloaded = System.Text.Json.JsonSerializer.Deserialize<AppSettings>(
            onDisk,
            new System.Text.Json.JsonSerializerOptions { PropertyNameCaseInsensitive = true });
        Assert.NotNull(reloaded);
        var encrypted = Assert.Single(reloaded!.LlmProfiles).ApiKey;
        Assert.StartsWith("dpapi:v1:", encrypted);
        Assert.Equal("sk-secret-encrypt", SecretProtector.Unprotect(encrypted));
    }

    [Fact]
    public async Task GithubToken_PersistsEncrypted_AndReloadsPlaintext()
    {
        // 落盘隔离到 temp 目录（SettingsFileFixture 已保证）
        var settings = new AppSettings();
        var vm = CreateVm(settings);
        vm.GithubToken = "ghp_每用户自己的令牌";

        vm.SaveCommand.Execute(null); // 触发 AppSettings.Save() 落盘

        // 内存单例持明文（运行态语义，随对话请求发送）
        Assert.Equal("ghp_每用户自己的令牌", settings.GithubToken);
        Assert.True(vm.HasSavedGithubToken);

        // 磁盘上应为 DPAPI 密文（非明文），绝不把令牌明文落盘
        var onDisk = System.IO.File.ReadAllText(AppSettings.ConfigPath);
        Assert.Contains("dpapi:v1:", onDisk);
        Assert.DoesNotContain("ghp_每用户自己的令牌", onDisk);

        // 模拟 App.LoadSettings 载入路径：反序列化后 Unprotect 还原明文
        var reloaded = System.Text.Json.JsonSerializer.Deserialize<AppSettings>(
            onDisk,
            new System.Text.Json.JsonSerializerOptions { PropertyNameCaseInsensitive = true });
        Assert.NotNull(reloaded);
        var encrypted = reloaded!.GithubToken;
        Assert.StartsWith("dpapi:v1:", encrypted);
        Assert.Equal("ghp_每用户自己的令牌", SecretProtector.Unprotect(encrypted));
    }

    [Fact]
    public async Task GithubToken_BlankSave_KeepsOriginal()
    {
        var settings = new AppSettings { GithubToken = "ghp_原值" };
        var vm = CreateVm(settings);

        // 输入框留空保存 = 保留原值（与 LLM API Key 同语义，避免误清）
        vm.GithubToken = null;
        vm.SaveCommand.Execute(null);

        Assert.Equal("ghp_原值", settings.GithubToken);
    }

    [Fact]
    public async Task ClearGithubToken_RemovesOnSave()
    {
        var settings = new AppSettings { GithubToken = "ghp_原值" };
        var vm = CreateVm(settings);
        vm.ClearGithubTokenCommand.Execute(null);

        Assert.True(vm.IsDirty);
        vm.SaveCommand.Execute(null);

        Assert.Null(settings.GithubToken);
        Assert.False(vm.HasSavedGithubToken);
    }

    [Fact]
    public void ApplyProfile_KeyDecryptFailed_ShowsWarning()
    {
        var profile = new LlmProfile
        {
            Id = "p1",
            Name = "坏 Key 档案",
            Provider = "openai",
            ApiKey = "sk-still-usable",
            KeyDecryptFailed = true,
        };
        var settings = new AppSettings { LlmProfiles = new List<LlmProfile> { profile } };
        var vm = CreateVm(settings);
        vm.SelectedProfile = vm.SavedProfiles[0];

        vm.ApplyProfileCommand.Execute(null);

        Assert.Contains("无法解密", vm.StatusMessage);
    }

    // ======================================================================
    // 官方服务商预设目录（Anthropic / Gemini 官方直连）
    // ======================================================================

    [Fact]
    public void PresetCatalog_ContainsAnthropicOfficial()
    {
        var preset = LlmPresetCatalog.All.FirstOrDefault(p => p.Id == "anthropic");
        Assert.NotNull(preset);
        Assert.Equal("anthropic", preset!.Provider);
        Assert.Equal("https://api.anthropic.com", preset.BaseUrl);
        Assert.False(string.IsNullOrWhiteSpace(preset.DefaultModel));
        Assert.Contains(preset.DefaultModel, preset.RecommendedModels);
        Assert.True(preset.RequiresApiKey);
    }

    [Fact]
    public void PresetCatalog_ContainsGeminiOfficial()
    {
        var preset = LlmPresetCatalog.All.FirstOrDefault(p => p.Id == "gemini");
        Assert.NotNull(preset);
        Assert.Equal("gemini", preset!.Provider);
        Assert.Equal("https://generativelanguage.googleapis.com", preset.BaseUrl);
        Assert.False(string.IsNullOrWhiteSpace(preset.DefaultModel));
        Assert.Contains(preset.DefaultModel, preset.RecommendedModels);
        Assert.True(preset.RequiresApiKey);
    }

    [Fact]
    public void SelectedPreset_AppliesAnthropicOfficial()
    {
        var vm = CreateVm(new AppSettings());
        var preset = vm.AvailablePresets.First(p => p.Id == "anthropic");

        vm.SelectedPreset = preset;

        Assert.Equal("anthropic", vm.LlmProvider);
        Assert.Equal("https://api.anthropic.com", vm.LlmBaseUrl);
        Assert.Equal(preset.DefaultModel, vm.LlmModel);
        Assert.Contains(preset.DefaultModel, vm.LlmModels.Select(i => i.Name));
    }

    [Fact]
    public void SelectedPreset_AppliesGeminiOfficial()
    {
        var vm = CreateVm(new AppSettings());
        var preset = vm.AvailablePresets.First(p => p.Id == "gemini");

        vm.SelectedPreset = preset;

        Assert.Equal("gemini", vm.LlmProvider);
        Assert.Equal("https://generativelanguage.googleapis.com", vm.LlmBaseUrl);
        Assert.Equal(preset.DefaultModel, vm.LlmModel);
        Assert.Contains(preset.DefaultModel, vm.LlmModels.Select(i => i.Name));
    }

    // ======================================================================
    // 档案一键体检（CheckAllProfiles）
    // ======================================================================

    [Fact]
    public async Task CheckAllProfiles_CollectsPerProfileResults()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnLlmTest = (req, _) => Task.FromResult(new LlmTestResult
        {
            Ok = req.Provider == "openai",
            Provider = req.Provider ?? "none",
            Model = req.Model ?? "",
            ElapsedMs = 120,
            Error = req.Provider == "openai" ? null : "API Key 无效或无权限",
        });

        var settings = new AppSettings
        {
            LlmProfiles = new List<LlmProfile>
            {
                new() { Id = "p1", Name = "DeepSeek", Provider = "openai", BaseUrl = "https://api.deepseek.com/v1", Model = "deepseek-chat", ApiKey = "sk-1" },
                new() { Id = "p2", Name = "Claude 官方", Provider = "anthropic", BaseUrl = "https://api.anthropic.com", Model = "claude-sonnet-4-5", ApiKey = "sk-2" },
            },
        };
        var vm = CreateVm(settings, fake);

        await vm.CheckAllProfilesCommand.ExecuteAsync(null);

        Assert.Equal(2, vm.ProfileCheckResults.Count);
        Assert.True(vm.HasProfileCheckResults);
        var ok = vm.ProfileCheckResults.First(r => r.Name == "DeepSeek");
        Assert.True(ok.Ok);
        Assert.Null(ok.ErrorDetail);
        Assert.Contains("可用", ok.StatusText);
        var bad = vm.ProfileCheckResults.First(r => r.Name == "Claude 官方");
        Assert.False(bad.Ok);
        Assert.Contains("API Key", bad.ErrorDetail);
        Assert.Contains("1/2", vm.StatusMessage);
    }

    [Fact]
    public async Task CheckAllProfiles_KeyDecryptFailed_MarksWithoutCallingBackend()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnLlmTest = (_, _) => Task.FromResult(new LlmTestResult { Ok = true, Provider = "openai" });
        var profile = new LlmProfile { Id = "p1", Name = "坏 Key", Provider = "openai", ApiKey = "sk-x", KeyDecryptFailed = true };
        var settings = new AppSettings { LlmProfiles = new List<LlmProfile> { profile } };
        var vm = CreateVm(settings, fake);

        await vm.CheckAllProfilesCommand.ExecuteAsync(null);

        var result = Assert.Single(vm.ProfileCheckResults);
        Assert.False(result.Ok);
        Assert.Contains("无法解密", result.ErrorDetail);
        Assert.Contains("0/1", vm.StatusMessage);
    }

    [Fact]
    public async Task CheckAllProfiles_NoProfiles_ShowsHintAndDoesNotCallBackend()
    {
        var fake = new FakeDoc2kbApiService();
        var called = false;
        fake.OnLlmTest = (_, _) => { called = true; return Task.FromResult(new LlmTestResult { Ok = true, Provider = "openai" }); };
        var vm = CreateVm(new AppSettings(), fake);

        await vm.CheckAllProfilesCommand.ExecuteAsync(null);

        Assert.False(called);
        Assert.Empty(vm.ProfileCheckResults);
        Assert.Contains("暂无档案", vm.StatusMessage);
    }

    // ======================================================================
    // 服务商 = 档案 新语义：模型列表 / 默认模型 / 下拉合并 / 模型增删
    // ======================================================================

    [Fact]
    public void SaveProfile_SavesProviderModelList_AndDefaultModel()
    {
        var settings = new AppSettings();
        var vm = CreateVm(settings);

        vm.LlmProvider = "openai";
        vm.LlmApiKey = "sk-models";
        vm.LlmBaseUrl = "https://api.deepseek.com/v1";
        // 模型候选 = 该服务商全部模型
        vm.LlmModels.Clear();
        vm.LlmModels.Add(new LlmModelItem("deepseek-chat"));
        vm.LlmModels.Add(new LlmModelItem("deepseek-reasoner"));
        // 默认模型 = 当前表单选中
        vm.LlmModel = "deepseek-chat";
        vm.ProfileNameInput = "DeepSeek 全家桶";

        vm.SaveProfileCommand.Execute(null);

        var provider = Assert.Single(vm.SavedProfiles);
        Assert.Equal("DeepSeek 全家桶", provider.Name);
        Assert.Equal("deepseek-chat", provider.Model); // 默认模型
        Assert.Equal(2, provider.Models.Count);
        Assert.Contains("deepseek-chat", provider.Models);
        Assert.Contains("deepseek-reasoner", provider.Models);
        // 已同步回 AppSettings（供落盘）
        Assert.Equal(2, settings.LlmProfiles[0].Models.Count);
    }

    [Fact]
    public async Task ApplyProfile_LoadsProviderModelList_IntoForm()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnUpdateConfig = (_, _) => Task.FromResult(new BackendConfig { Notice = null });

        var profile = new LlmProfile
        {
            Id = "p1",
            Name = "DeepSeek",
            Provider = "openai",
            BaseUrl = "https://api.deepseek.com/v1",
            Model = "deepseek-chat", // 默认模型
            Models = new List<string> { "deepseek-chat", "deepseek-reasoner", "deepseek-coder" },
            ApiKey = "sk-p",
        };
        var settings = new AppSettings { LlmProfiles = new List<LlmProfile> { profile } };
        var vm = CreateVm(settings, fake);
        vm.SelectedProfile = vm.SavedProfiles[0];

        await vm.ApplyProfileCommand.ExecuteAsync(null);

        // 模型下拉候选 = 服务商全部模型
        Assert.Equal(3, vm.LlmModels.Count);
        Assert.Contains("deepseek-chat", vm.LlmModels.Select(i => i.Name));
        Assert.Contains("deepseek-reasoner", vm.LlmModels.Select(i => i.Name));
        Assert.Contains("deepseek-coder", vm.LlmModels.Select(i => i.Name));
        // 默认模型已回填
        Assert.Equal("deepseek-chat", vm.LlmModel);
    }

    [Fact]
    public void ProviderOptions_MergeBuiltInPresets_AndCustomProviders()
    {
        var settings = new AppSettings
        {
            LlmProfiles = new List<LlmProfile>
            {
                new() { Id = "p1", Name = "我的 DeepSeek", Provider = "openai" },
            },
        };
        var vm = CreateVm(settings);

        // 合并下拉 = 内置预设 + 自定义服务商
        Assert.Equal(LlmPresetCatalog.All.Count + 1, vm.ProviderOptions.Count);
        Assert.Contains(vm.ProviderOptions, o => o.IsBuiltIn && o.Preset?.Id == "deepseek");
        Assert.Contains(vm.ProviderOptions, o => o.IsCustom && o.Profile?.Id == "p1");
        // 默认选中：有 ActiveProfileId 时优先；无则首个
        Assert.NotNull(vm.SelectedProviderOption);
    }

    [Fact]
    public void AddModelToProvider_AddsToCandidateList_AndDeduplicates()
    {
        var vm = CreateVm(new AppSettings());
        vm.LlmModels.Add(new LlmModelItem("deepseek-chat"));
        vm.LlmModel = "deepseek-coder";

        vm.AddModelToProviderCommand.Execute(null);

        Assert.Contains("deepseek-coder", vm.LlmModels.Select(i => i.Name));

        // 重复添加不生效
        vm.AddModelToProviderCommand.Execute(null);
        Assert.Equal(2, vm.LlmModels.Count);
    }

    [Fact]
    public void RemoveModelFromProvider_RemovesSelectedCandidate()
    {
        var vm = CreateVm(new AppSettings());
        vm.LlmModels.Add(new LlmModelItem("deepseek-chat"));
        vm.LlmModels.Add(new LlmModelItem("deepseek-reasoner"));
        vm.LlmModel = "deepseek-chat";
        vm.SelectedModelCandidate = vm.LlmModels.First(i => i.Name == "deepseek-chat");

        vm.RemoveModelFromProviderCommand.Execute(null);

        Assert.Single(vm.LlmModels);
        Assert.DoesNotContain("deepseek-chat", vm.LlmModels.Select(i => i.Name));
    }
}