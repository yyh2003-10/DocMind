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

    [Fact]
    public async Task SendAsync_TopKNotSent_BackendDecides()
    {
        // 回归防护：对话页不得用硬编码 TopK=5 覆盖设置页「RAG Top-K」
        // （此前 ChatRequest.TopK 恒为 5，设置页改引用数后对话页实际仍检索 5 条）
        var fake = CreateFake();
        ChatRequest? captured = null;
        fake.OnChat = (req, _) =>
        {
            captured = req;
            return Task.FromResult(MakeResponse());
        };

        var vm = CreateVm(fake);
        vm.InputText = "查询";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.NotNull(captured);
        Assert.Null(captured.TopK); // null = 由后端按 rag_top_k 配置决定
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

    // ======================================================================
    // 对话内快速切换模型（ChatRequest.Model）
    // ======================================================================

    [Fact]
    public async Task SendAsync_DefaultModel_DoesNotPassModel()
    {
        var fake = CreateFake();
        ChatRequest? captured = null;
        fake.OnChat = (req, _) => { captured = req; return Task.FromResult(MakeResponse()); };

        var vm = CreateVm(fake);
        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.Equal(ChatViewModel.DefaultModelLabel, vm.SelectedModel); // 默认选中「默认」
        Assert.Null(captured!.Model);
    }

    [Fact]
    public async Task SendAsync_SelectedModel_PassedToRequest()
    {
        var fake = CreateFake();
        ChatRequest? captured = null;
        fake.OnChat = (req, _) => { captured = req; return Task.FromResult(MakeResponse()); };

        var vm = CreateVm(fake);
        vm.SelectedModel = "qwen2.5:7b";
        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.Equal("qwen2.5:7b", captured!.Model);
    }

    [Fact]
    public async Task RefreshModels_FillsAvailableModelsAndKeepsDefault()
    {
        var fake = CreateFake();
        fake.OnLlmModels = (_, _) => Task.FromResult(new LlmModelsResult
        {
            Ok = true,
            Provider = "ollama",
            Models = new[] { "llama3.2:latest", "qwen2.5:7b" },
        });

        var vm = CreateVm(fake);
        await vm.RefreshModelsCommand.ExecuteAsync(null);

        Assert.Equal(3, vm.AvailableModels.Count); // 默认伪值 + 2 个模型
        Assert.Equal(ChatViewModel.DefaultModelLabel, vm.AvailableModels[0]);
        Assert.Contains("qwen2.5:7b", vm.AvailableModels);
        Assert.Equal(ChatViewModel.DefaultModelLabel, vm.SelectedModel); // 不改变当前选择
    }

    [Fact]
    public async Task RefreshModels_Failure_ShowsErrorAndKeepsList()
    {
        var fake = CreateFake();
        fake.OnLlmModels = (_, _) => Task.FromResult(new LlmModelsResult
        {
            Ok = false,
            Provider = "ollama",
            Error = "无法连接 Ollama 服务",
        });

        var vm = CreateVm(fake);
        await vm.RefreshModelsCommand.ExecuteAsync(null);

        Assert.Single(vm.AvailableModels); // 仅剩默认伪值
        Assert.Contains("获取模型列表失败", vm.StatusMessage);
    }

    // ======================================================================
    // 重新生成
    // ======================================================================

    [Fact]
    public async Task Regenerate_RemovesOldAnswerAndResendsLastUserQuery()
    {
        var fake = CreateFake();
        var queries = new List<string>();
        fake.OnChat = (req, _) => { queries.Add(req.Query); return Task.FromResult(MakeResponse(answer: "新回答")); };

        var vm = CreateVm(fake);
        vm.InputText = "第一个问题";
        await vm.SendCommand.ExecuteAsync(null);
        vm.InputText = "第二个问题";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.Equal(4, vm.Messages.Count); // 2 轮 user+assistant

        await vm.RegenerateCommand.ExecuteAsync(null);

        // 旧回答被移除，只重发最后一个用户问题（不重复添加 user 消息）
        Assert.Equal(["第一个问题", "第二个问题", "第二个问题"], queries);
        Assert.Equal(4, vm.Messages.Count); // 2 user + 1 旧assistant(第一轮) + 1 新assistant
        Assert.Equal("新回答", vm.Messages[^1].Content);
        Assert.Equal("第二个问题", vm.Messages[^2].Content);
    }

    [Fact]
    public async Task Regenerate_NotAvailable_WhenLastMessageIsUser()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse());
        var vm = CreateVm(fake);

        Assert.False(vm.RegenerateCommand.CanExecute(null));

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.True(vm.RegenerateCommand.CanExecute(null));
        Assert.True(vm.Messages[^1].ShowRegenerate);
    }

    // ======================================================================
    // 历史会话（持久化）
    // ======================================================================

    [Fact]
    public async Task LoadSessions_PopulatesSessionList()
    {
        var fake = CreateFake();
        fake.OnListChats = (_, _) => Task.FromResult(new ChatSessionListResponse
        {
            Total = 2,
            Chats = new[]
            {
                new ChatSessionSummary { ChatId = "chat-a", Title = "会话 A", MessageCount = 2 },
                new ChatSessionSummary { ChatId = "chat-b", Title = "会话 B", MessageCount = 4 },
            },
        });

        var vm = CreateVm(fake);
        await vm.SessionsLoadedForTestAsync();

        Assert.Equal(2, vm.Sessions.Count);
        Assert.Equal("chat-a", vm.Sessions[0].ChatId);
        Assert.Contains("2 条", vm.Sessions[0].Display);
    }

    [Fact]
    public async Task SelectSession_LoadsMessagesAndContinuesWithSameChatId()
    {
        var fake = CreateFake();
        fake.OnListChats = (_, _) => Task.FromResult(new ChatSessionListResponse
        {
            Chats = new[] { new ChatSessionSummary { ChatId = "chat-old", Title = "旧会话", MessageCount = 2 } },
        });
        fake.OnGetChat = (id, _) => Task.FromResult(new ChatSessionDetail
        {
            ChatId = id,
            Title = "旧会话",
            Messages = new[]
            {
                new ChatSessionMessage { Role = "user", Content = "历史问题" },
                new ChatSessionMessage { Role = "assistant", Content = "历史回答" },
            },
        });
        string? seenChatId = null;
        fake.OnChat = (req, _) => { seenChatId = req.ChatId; return Task.FromResult(MakeResponse()); };

        var vm = CreateVm(fake);
        await vm.SessionsLoadedForTestAsync();
        vm.SelectedSession = vm.Sessions[0];
        await vm.SessionLoadedForTestAsync();

        // 历史消息载入视图
        Assert.Equal(2, vm.Messages.Count);
        Assert.Equal("历史回答", vm.Messages[^1].Content);

        // 续聊沿用旧会话 chatId（后端从 DB 恢复多轮上下文）
        vm.InputText = "继续问";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.Equal("chat-old", seenChatId);
    }

    [Fact]
    public async Task DeleteSession_RemovesFromList_AndClearsCurrentConversation()
    {
        var fake = CreateFake();
        fake.OnListChats = (_, _) => Task.FromResult(new ChatSessionListResponse
        {
            Chats = new[] { new ChatSessionSummary { ChatId = "chat-x", Title = "待删", MessageCount = 2 } },
        });
        string? deletedId = null;
        fake.OnDeleteChat = (id, _) => { deletedId = id; return Task.CompletedTask; };

        var vm = CreateVm(fake);
        await vm.SessionsLoadedForTestAsync();
        vm.SelectedSession = vm.Sessions[0];
        vm.Messages.Add(new ChatMessage { Role = "user", Content = "msg" });

        await vm.DeleteSessionCommand.ExecuteAsync(vm.Sessions[0]);

        Assert.Equal("chat-x", deletedId);
        Assert.Empty(vm.Sessions);
        Assert.Empty(vm.Messages); // 删除当前会话 → 视图清空
    }

    [Fact]
    public async Task NewChat_ClearsMessagesAndSessionSelection()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse());
        var vm = CreateVm(fake);

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.NotEmpty(vm.Messages);

        vm.NewChatCommand.Execute(null);

        Assert.Empty(vm.Messages);
        Assert.Null(vm.SelectedSession);
    }

    // ======================================================================
    // Markdown 渲染
    // ======================================================================

    [Fact]
    public async Task SendAsync_MarkdownContent_RendersFlowDocument()
    {
        var fake = CreateFake();
        var markdown = "这是**加粗**文本\n\n- 列表项1\n- 列表项2\n\n```csharp\nvar x = 1;\n```";
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse(answer: markdown));
        var vm = CreateVm(fake);

        vm.InputText = "测试 markdown 渲染";
        await vm.SendCommand.ExecuteAsync(null);

        var assistantMsg = vm.Messages.LastOrDefault(m => m.Role == "assistant");
        Assert.NotNull(assistantMsg);
        Assert.NotNull(assistantMsg.RenderedDocument);
        Assert.NotEmpty(assistantMsg.RenderedDocument.Blocks);
    }

    [Fact]
    public async Task SendAsync_UserMessage_HasNullRenderedDocument()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse());
        var vm = CreateVm(fake);

        vm.InputText = "普通问题";
        await vm.SendCommand.ExecuteAsync(null);

        var userMsg = vm.Messages.FirstOrDefault(m => m.Role == "user");
        Assert.NotNull(userMsg);
        Assert.Null(userMsg.RenderedDocument);
    }

    // ======================================================================
    // 引用来源点击
    // ======================================================================

    [Fact]
    public async Task OpenSourceCommand_FiresSearchRequestedEvent()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse());
        var vm = CreateVm(fake);

        vm.InputText = "测试";
        await vm.SendCommand.ExecuteAsync(null);

        var assistantMsg = vm.Messages.LastOrDefault(m => m.Role == "assistant");
        Assert.NotNull(assistantMsg);
        Assert.NotNull(assistantMsg.Sources);
        var src = assistantMsg.Sources.First();

        SourceRef? captured = null;
        vm.SourceSearchRequested += (s) => captured = s;

        vm.OpenSourceCommand.Execute(src);

        Assert.NotNull(captured);
        Assert.Equal(src.Index, captured.Index);
        Assert.Equal(src.Source, captured.Source);
    }

    // ======================================================================
    // 消息撤回与回填
    // ======================================================================

    [Fact]
    public async Task WithdrawCommand_RemovesUserAndAssistantMessages_AndRefillsInput()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse("这是回答"));
        var vm = CreateVm(fake);

        vm.InputText = "我想撤回的问题";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.Equal(2, vm.Messages.Count);
        Assert.True(vm.Messages[0].ShowWithdraw);

        // 撤回该用户消息
        vm.WithdrawCommand.Execute(vm.Messages[0]);

        Assert.Empty(vm.Messages);
        Assert.Equal("我想撤回的问题", vm.InputText);
    }
}