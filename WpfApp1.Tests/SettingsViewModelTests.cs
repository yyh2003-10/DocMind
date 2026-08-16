using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using DocMind;

namespace WpfApp1.Tests;

/// <summary>
/// SettingsViewModel 单元测试：LLM 字段初始化、IsDirty 追踪、保存推送、Revert 恢复。
/// </summary>
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
        var gpuWarning = new GpuWarningViewModel();
        return new SettingsViewModel(appSettings, notifications, themeService, fake, gpuWarning);
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
        fake.OnGetConfig = _ => Task.FromResult(new BackendConfig
        {
            LlmProvider = "none",
            LlmApiKeyConfigured = false,
        });
        var vm = CreateVm(new AppSettings(), fake);

        await vm.TestConnectionCommand.ExecuteAsync(null);

        Assert.Contains("后端连接正常", vm.StatusMessage);
        Assert.False(vm.IsTestingConnection);
    }

    [Fact]
    public async Task TestConnection_LlmConfigured_CallsChat()
    {
        var chatCalled = false;
        var fake = new FakeDoc2kbApiService();
        fake.OnGetHealth = _ => Task.FromResult(new HealthStatus { Status = "ok" });
        fake.OnGetConfig = _ => Task.FromResult(new BackendConfig
        {
            LlmProvider = "openai",
            LlmApiKeyConfigured = true,
        });
        fake.OnChat = (_, _) =>
        {
            chatCalled = true;
            return Task.FromResult(new ChatResponse
            {
                Answer = "连接成功",
                Model = "test-model",
                Provider = "mock",
            });
        };
        var vm = CreateVm(new AppSettings(), fake);

        await vm.TestConnectionCommand.ExecuteAsync(null);

        Assert.Contains("连接成功", vm.StatusMessage);
        Assert.True(chatCalled);
        Assert.False(vm.IsTestingConnection);
    }

    [Fact]
    public async Task TestConnection_LlmChatFails_ShowsLlmError()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetHealth = _ => Task.FromResult(new HealthStatus { Status = "ok" });
        fake.OnGetConfig = _ => Task.FromResult(new BackendConfig
        {
            LlmProvider = "openai",
            LlmApiKeyConfigured = true,
        });
        fake.OnChat = (_, _) => throw new ApiException("AUTH_ERROR", "API Key 无效");
        var vm = CreateVm(new AppSettings(), fake);

        await vm.TestConnectionCommand.ExecuteAsync(null);

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
}