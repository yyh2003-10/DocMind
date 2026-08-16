using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Tests;

/// <summary>
/// ChatViewModel 单元测试：发送、多轮对话、集合、错误处理、加载状态。
/// 不依赖真实 HTTP，使用 FakeDoc2kbApiService 注入可控响应。
/// </summary>
public class ChatViewModelTests
{
    private static ChatViewModel CreateVm(FakeDoc2kbApiService fake)
        => new(fake);

    private static FakeDoc2kbApiService CreateFake()
    {
        var fake = new FakeDoc2kbApiService();
        // 默认 GetStatsAsync 返回一个 default 集合（避免加载集合时抛异常）
        fake.OnGetStats = (_, _) =>
            Task.FromResult(new Stats
            {
                TotalDocuments = 0,
                TotalChunks = 0,
                Collections = new Dictionary<string, int[]> { { "default", [0, 0, 0] } },
            });
        return fake;
    }

    private static ChatResponse MakeResponse(string answer = "回答", string chatId = "chat-test123", int elapsedMs = 100)
        => new()
        {
            Answer = answer,
            ChatId = chatId,
            Model = "mock-model",
            Provider = "mock",
            TotalChunks = 2,
            ElapsedMs = elapsedMs,
            Sources = [new SourceRef { Index = 1, Source = "doc.pdf", Page = 1, Score = 0.9 }],
        };

    // ======================================================================
    // 发送消息
    // ======================================================================

    [Fact]
    public async Task SendAsync_AddsUserMessageAndLoadingThenReplaces()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse());
        var vm = CreateVm(fake);

        vm.InputText = "你好";
        await vm.SendCommand.ExecuteAsync(null);

        // 用户消息存在
        Assert.Contains(vm.Messages, m => m.Role == "user" && m.Content == "你好");
        // 加载占位已被替换为真实回答
        var assistantMsg = vm.Messages.LastOrDefault(m => m.Role == "assistant");
        Assert.NotNull(assistantMsg);
        Assert.Equal("回答", assistantMsg.Content);
        Assert.False(assistantMsg.IsLoading);
        Assert.True(assistantMsg.HasSources);
        Assert.NotNull(assistantMsg.Model);
    }

    [Fact]
    public async Task SendAsync_ClearsInputText()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse());
        var vm = CreateVm(fake);

        vm.InputText = "测试消息";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.Equal(string.Empty, vm.InputText);
    }

    [Fact]
    public async Task SendAsync_SetsIsBusyDuringRequest()
    {
        var fake = CreateFake();
        var tcs = new TaskCompletionSource<ChatResponse>();
        fake.OnChat = (_, _) => tcs.Task;

        var vm = CreateVm(fake);
        vm.InputText = "忙";

        // 启动发送，不等完成
        var sendTask = vm.SendCommand.ExecuteAsync(null);

        // 请求中应该 busy
        Assert.True(vm.IsBusy);

        // 完成请求
        tcs.SetResult(MakeResponse());
        await sendTask;

        Assert.False(vm.IsBusy);
    }

    [Fact]
    public async Task SendAsync_PassesChatIdForMultiTurn()
    {
        var fake = CreateFake();
        var receivedChatIds = new List<string?>();
        fake.OnChat = (req, _) =>
        {
            receivedChatIds.Add(req.ChatId);
            return Task.FromResult(MakeResponse(chatId: "chat-session-1"));
        };

        var vm = CreateVm(fake);

        // 第一轮：chatId=null
        vm.InputText = "第一轮";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.Null(receivedChatIds[0]); // 首轮 null

        // 第二轮：应带上第一轮返回的 chat_id
        vm.InputText = "第二轮";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.Equal("chat-session-1", receivedChatIds[1]);
    }

    [Fact]
    public async Task SendAsync_PassesSelectedCollections()
    {
        var fake = CreateFake();
        // 返回多个集合
        fake.OnGetStats = (_, _) =>
            Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]>
                {
                    { "default", [0, 0, 0] },
                    { "docs-a", [1, 5, 1000] },
                    { "docs-b", [1, 3, 500] },
                },
            });

        ChatRequest? captured = null;
        fake.OnChat = (req, _) =>
        {
            captured = req;
            return Task.FromResult(MakeResponse());
        };

        var vm = CreateVm(fake);
        // 等集合加载完成
        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        // 勾选 docs-a 和 docs-b
        foreach (var c in vm.Collections)
        {
            if (c.Name == "docs-a" || c.Name == "docs-b")
                c.IsSelected = true;
        }

        vm.InputText = "查询";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.NotNull(captured);
        Assert.Contains("docs-a", captured.Collections!);
        Assert.Contains("docs-b", captured.Collections!);
        // default 集合默认勾选，但这里我们只勾选了 docs-a 和 docs-b
        // 注意：默认勾选 default 后，SelectedCollections 中包含 default
        // 这里我们只验证 docs-a 和 docs-b 在列表中
        Assert.Equal(3, captured.Collections!.Count); // default + docs-a + docs-b
    }

    // ======================================================================
    // 错误处理
    // ======================================================================

    [Fact]
    public async Task SendAsync_ApiException_ShowsError()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => throw new ApiException("BAD_REQUEST", "请求参数错误");
        var vm = CreateVm(fake);

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        var lastMsg = vm.Messages.Last();
        Assert.Contains("API 错误", lastMsg.Content);
        Assert.Contains("请求参数错误", lastMsg.Content);
        Assert.Equal("assistant", lastMsg.Role);
    }

    [Fact]
    public async Task SendAsync_BackendConnectionException_ShowsError()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => throw new BackendConnectionException("后端不可达");
        var vm = CreateVm(fake);

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        var lastMsg = vm.Messages.Last();
        Assert.Contains("后端不可达", lastMsg.Content);
        Assert.Contains("后端不可达", lastMsg.Content);
    }

    [Fact]
    public async Task SendAsync_GeneralException_ShowsError()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => throw new InvalidOperationException("未知异常");
        var vm = CreateVm(fake);

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        var lastMsg = vm.Messages.Last();
        Assert.Contains("错误", lastMsg.Content);
        Assert.Contains("未知异常", lastMsg.Content);
    }

    [Fact]
    public async Task SendAsync_ErrorReplacesLoadingPlaceholder()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => throw new ApiException("SERVER_ERROR", "服务异常");
        var vm = CreateVm(fake);

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        // 不应该有 loading 残留
        Assert.DoesNotContain(vm.Messages, m => m.IsLoading);
        // 应该只有一条 user 消息 + 一条 assistant 错误消息
        Assert.Equal(2, vm.Messages.Count);
    }

    // ======================================================================
    // 集合加载
    // ======================================================================

    [Fact]
    public async Task LoadCollectionsAsync_LoadsFromApi()
    {
        var fake = CreateFake();
        fake.OnGetStats = (_, _) =>
            Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]>
                {
                    { "default", [5, 100, 50000] },
                    { "docs-design", [2, 30, 15000] },
                },
            });

        var vm = CreateVm(fake);
        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        Assert.Equal(2, vm.Collections.Count);
        // default 集合默认勾选
        Assert.True(vm.Collections.First(c => c.Name == "default").IsSelected);
        // 其他集合不勾选
        Assert.False(vm.Collections.First(c => c.Name == "docs-design").IsSelected);
    }

    [Fact]
    public async Task LoadCollectionsAsync_ApiFailure_ShowsDefault()
    {
        var fake = CreateFake();
        fake.OnGetStats = (_, _) => throw new InvalidOperationException("网络错误");
        var vm = CreateVm(fake);

        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        // 失败时 fallback 到 default 集合
        Assert.Single(vm.Collections);
        Assert.Equal("default", vm.Collections[0].Name);
        Assert.True(vm.Collections[0].IsSelected);
    }

    [Fact]
    public async Task LoadCollectionsAsync_PreservesUserAddedCollections()
    {
        var fake = CreateFake();
        // 首次加载只有 default
        fake.OnGetStats = (_, _) =>
            Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]> { { "default", [0, 0, 0] } },
            });

        var vm = CreateVm(fake);
        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        // 用户手动添加一个不存在于后端的集合
        fake.OnGetStats = (_, _) =>
            Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]> { { "default", [0, 0, 0] } }, // 仍然没有 docs-manual
            });

        // 模拟用户通过 AddCollection 添加（在 AddCollection 失败回退中添加）
        vm.Collections.Add(new CollectionItem { Name = "docs-manual", IsSelected = true });
        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        // docs-manual 应被保留
        Assert.Contains(vm.Collections, c => c.Name == "docs-manual");
        Assert.Equal(2, vm.Collections.Count);
    }

    // ======================================================================
    // 添加集合
    // ======================================================================

    [Fact]
    public async Task AddCollectionAsync_CreatesAndSelects()
    {
        var fake = CreateFake();
        fake.OnGetStats = (_, _) =>
            Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]> { { "default", [0, 0, 0] } },
            });
        fake.OnCreateCollection = (name, _) =>
            Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]>
                {
                    { "default", [0, 0, 0] },
                    { name, [0, 0, 0] },
                },
            });

        var vm = CreateVm(fake);
        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        vm.NewCollectionName = "my-kb";
        await vm.AddCollectionCommand.ExecuteAsync(null);

        // 新集合应被添加并勾选
        var added = vm.Collections.FirstOrDefault(c => c.Name == "my-kb");
        Assert.NotNull(added);
        Assert.True(added.IsSelected);
        // default 也应勾选
        Assert.True(vm.Collections.First(c => c.Name == "default").IsSelected);
    }

    [Fact]
    public async Task AddCollectionAsync_Failure_AddsLocally()
    {
        var fake = CreateFake();
        fake.OnCreateCollection = (_, _) => throw new InvalidOperationException("创建失败");

        var vm = CreateVm(fake);
        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        vm.NewCollectionName = "offline-kb";
        await vm.AddCollectionCommand.ExecuteAsync(null);

        // 失败时仍应在本地列表中
        Assert.Contains(vm.Collections, c => c.Name == "offline-kb");
        Assert.True(vm.Collections.First(c => c.Name == "offline-kb").IsSelected);
    }

    // ======================================================================
    // 清空对话
    // ======================================================================

    [Fact]
    public async Task Clear_RemovesAllMessagesAndResetsChatId()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse(chatId: "chat-clear-test"));
        var vm = CreateVm(fake);

        vm.InputText = "消息";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.NotEmpty(vm.Messages);

        vm.ClearCommand.Execute(null);

        Assert.Empty(vm.Messages);
        // 第二轮应发送新的 chat_id（null 表示新建会话）
        ChatRequest? captured = null;
        fake.OnChat = (req, _) =>
        {
            captured = req;
            return Task.FromResult(MakeResponse());
        };
        vm.InputText = "新问题";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.Null(captured!.ChatId);
    }

    // ======================================================================
    // 命令可用性
    // ======================================================================

    [Fact]
    public async Task SendCommand_Disabled_WhenBusyOrNoInput()
    {
        var fake = CreateFake();
        fake.OnGetStats = (_, _) =>
            Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]>(),
            });
        var vm = CreateVm(fake);
        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        // 无输入时不可用
        Assert.False(vm.SendCommand.CanExecute(null));

        // 有输入时可用
        vm.InputText = "你好";
        Assert.True(vm.SendCommand.CanExecute(null));

        // Busy 时不可用
        vm.IsBusy = true;
        Assert.False(vm.SendCommand.CanExecute(null));
    }

    [Fact]
    public async Task HasSelectedCollection_ReflectsCheckboxState()
    {
        var fake = CreateFake();
        // 返回空集合，不勾选任何项
        fake.OnGetStats = (_, _) =>
            Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]>(),
            });
        var vm = CreateVm(fake);
        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        // 添加两个不勾选的集合
        vm.Collections.Add(new CollectionItem { Name = "kb1", IsSelected = false });
        vm.Collections.Add(new CollectionItem { Name = "kb2", IsSelected = false });

        Assert.False(vm.HasSelectedCollection);

        // 勾选一个后应变为 true
        vm.Collections[0].IsSelected = true;
        Assert.True(vm.HasSelectedCollection);
    }

    [Fact]
    public async Task ShowEmptyGuide_TrueOnlyWhenNotBusyAndNoMessages()
    {
        var fake = CreateFake();
        fake.OnGetStats = (_, _) =>
            Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]>(),
            });
        var vm = CreateVm(fake);
        await vm.LoadCollectionsCommand.ExecuteAsync(null);

        Assert.True(vm.ShowEmptyGuide);

        vm.IsBusy = true;
        Assert.False(vm.ShowEmptyGuide);

        vm.IsBusy = false;
        vm.Messages.Add(new ChatMessage { Role = "user", Content = "hi" });
        Assert.False(vm.ShowEmptyGuide);
    }
}