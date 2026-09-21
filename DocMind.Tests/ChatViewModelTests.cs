using System.Windows;
using System.Windows.Documents;
using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Tests;

/// <summary>
/// ChatViewModel 单元测试：发送、多轮对话、集合、错误处理、加载状态。
/// 不依赖真实 HTTP，使用 FakeDoc2kbApiService 注入可控响应。
/// 标记 SettingsFile 集合：档案切换会落盘 appsettings.json，隔离到 temp 目录。
/// </summary>
[Collection("SettingsFile")]
public class ChatViewModelTests
{
    private static ChatViewModel CreateVm(FakeDoc2kbApiService fake)
        // 默认已配置 LLM（模拟正常可用环境：provider + Key 齐全）；「未配置」场景的测试需显式传 new AppSettings()
        => new(fake, null, new AppSettings { LlmProvider = "openai", LlmApiKey = "test-key" });

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

        var settings = new AppSettings
        {
            LlmProfiles = new List<LlmProfile>
            {
                new() { Id = "p1", Name = "Ollama 本地", Provider = "ollama", Model = "qwen2.5:7b", Models = new List<string> { "qwen2.5:7b" }, ApiKey = "k1" },
            },
        };
        var vm = new ChatViewModel(fake, null, settings);
        // 点选某服务商的模型（非默认项）→ 请求携带该模型
        vm.SelectedModelChoice = vm.ModelChoices.First(c => c.Model == "qwen2.5:7b");
        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.Equal("qwen2.5:7b", captured!.Model);
    }

    [Fact]
    public async Task RefreshModels_FillsDefaultProviderGroupAndKeepsSelection()
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

        // 拉取结果并入「默认提供商」分组（Provider 为空、非默认伪项）
        Assert.Contains(vm.ModelChoices, c => c.Provider is null && !c.IsDefault && c.Model == "qwen2.5:7b");
        Assert.Contains(vm.ModelChoices, c => c.Provider is null && !c.IsDefault && c.Model == "llama3.2:latest");
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

        Assert.Single(vm.ModelChoices); // 仅剩默认伪项，列表不变
        Assert.True(vm.ModelChoices[0].IsDefault);
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

    [Fact]
    public void MarkdownHeading_DownscaledToBodySize_NotReportLike()
    {
        // 气泡里 ###/#### 标题不应渲染成 18px+ 大标题（文档报告感），
        // 现代简约层级：H1-H3 以小步长降级（上限 17px SemiBold），正文保持 15px
        RunOnSta(() =>
        {
            var msg = new ChatMessage { Role = "assistant" };
            msg.Content = "### 标题\n\n#### 副标题\n\n正文内容";

            Assert.NotNull(msg.RenderedDocument);
            var paras = FindParagraphs(msg.RenderedDocument!).ToList();
            var heading = paras.FirstOrDefault(p => p.Inlines.OfType<Run>().Any(r => r.Text.Contains("标题")));
            Assert.NotNull(heading);
            Assert.True(heading!.FontSize <= 17, $"标题字号应为 17 以内，实际 {heading.FontSize}");
            Assert.NotEqual(FontWeights.Bold, heading.FontWeight);
        });
    }

    [Fact]
    public void MarkdownCodeBlock_ContentIsRendered_NotDropped()
    {
        // 回归防护：AI 写代码类回答曾出现「气泡里只剩标题、代码块整块消失」。
        // 仅断言 Blocks 非空测不出代码块被丢，必须校验代码文本确实渲染到了 FlowDocument。
        RunOnSta(() =>
        {
            var msg = new ChatMessage { Role = "assistant" };
            msg.Content = "### 代码示例\n\n```python\nimport os\nprint(os.getcwd())\n```\n\n结尾说明。";

            Assert.NotNull(msg.RenderedDocument);
            var paras = FindParagraphs(msg.RenderedDocument!).ToList();
            var texts = paras.Select(TextOf).ToList();

            Assert.Contains(texts, t => t.Contains("代码示例"));
            Assert.Contains(texts, t => t.Contains("os.getcwd"));
            Assert.Contains(texts, t => t.Contains("结尾说明"));
        });
    }

    [Fact]
    public async Task SendAsync_ThinkingFrameMidStream_DoesNotWipeAlreadyStreamedContent()
    {
        // 回归防护：推理模型的 reasoning_content、上下文溢出重试状态帧、生成结束后的
        // Agent 自省帧，都会在正文流到一半时到达。此前 ApplyToken 用
        // IsThinkingInProgress/IsWaitingForFirstToken 判定「首帧」，这些帧会把标志复位成
        // True，导致下一个 token 走 Content = token 覆盖分支，把已累积的正文整体抹掉，
        // 表现为「回答只剩标题 / 只剩最后一段」。
        var fake = CreateFake();
        fake.OnChatStreamWithStatus = (_, onToken, onDone, onStatus, onThinking, _) =>
        {
            onStatus?.Invoke("正在检索知识库...");
            onToken("### 代码示例\n\n");
            // 正文流到一半时插入推理链帧（此前会触发正文被清空）
            onThinking?.Invoke("让我先想想用哪种写法");
            onToken("```python\nprint('hi')\n```");
            onDone(new ChatStreamResult { ChatId = "chat-test123", Model = "mock-model", Provider = "mock" });
            return Task.FromResult(new ChatStreamResult { ChatId = "chat-test123", Model = "mock-model", Provider = "mock" });
        };
        var vm = CreateVm(fake);

        vm.InputText = "写个 python 程序";
        await vm.SendCommand.ExecuteAsync(null);

        var assistantMsg = vm.Messages.LastOrDefault(m => m.Role == "assistant");
        Assert.NotNull(assistantMsg);
        Assert.Contains("代码示例", assistantMsg!.Content);
        Assert.Contains("print('hi')", assistantMsg.Content);
    }

    private static string TextOf(Paragraph p)
        => string.Concat(p.Inlines.SelectMany(FlattenRuns).Select(r => r.Text));

    private static IEnumerable<Run> FlattenRuns(Inline inline) => inline switch
    {
        Run r => new[] { r },
        Hyperlink h => h.Inlines.SelectMany(FlattenRuns),
        Span sp => sp.Inlines.SelectMany(FlattenRuns),
        _ => Enumerable.Empty<Run>(),
    };

    private static IEnumerable<Paragraph> FindParagraphs(FlowDocument doc)
        => doc.Blocks.SelectMany(FindParagraphs);

    private static IEnumerable<Paragraph> FindParagraphs(Block block) => block switch
    {
        Paragraph p => new[] { p },
        List l => l.ListItems.SelectMany(li => li.Blocks.SelectMany(FindParagraphs)),
        Table t => t.RowGroups.SelectMany(rg => rg.Rows)
                              .SelectMany(r => r.Cells)
                              .SelectMany(c => c.Blocks)
                              .SelectMany(FindParagraphs),
        Section s => s.Blocks.SelectMany(FindParagraphs),
        _ => Enumerable.Empty<Paragraph>(),
    };

    [Fact]
    public void HttpMarkdownLink_IsClickableWithSafeUri()
    {
        // Markdig.Wpf 渲染 Hyperlink 要求 STA 线程，故在专用 STA 线程上构造消息
        RunOnSta(() =>
        {
            var markdown = "官方资料见[台达官网](https://www.deltaww.com/zh-CN/)和[手册](https://filecenter.deltaww.com/asda-b3.pdf)。";
            var msg = new ChatMessage { Role = "assistant" };
            msg.Content = markdown;

            Assert.NotNull(msg.RenderedDocument);
            var links = FindHyperlinks(msg.RenderedDocument!).ToList();
            // 两个 http(s) 链接都保留可点击导航（NavigateUri 未被移除）
            Assert.Equal(2, links.Count);
            Assert.All(links, l =>
            {
                Assert.NotNull(l.NavigateUri);
                Assert.True(l.NavigateUri!.Scheme == Uri.UriSchemeHttp || l.NavigateUri.Scheme == Uri.UriSchemeHttps);
            });
        });
    }

    [Fact]
    public void UnsafeMarkdownLinks_NotClickable()
    {
        RunOnSta(() =>
        {
            var markdown =
                "危险链接：\n" +
                "- [脚本](javascript:alert(1))\n" +
                "- [本地文件](file:///C:/secret.txt)\n" +
                "- [相对路径](docs/guide)\n";
            var msg = new ChatMessage { Role = "assistant" };
            msg.Content = markdown;

            Assert.NotNull(msg.RenderedDocument);
            var links = FindHyperlinks(msg.RenderedDocument!).ToList();
            Assert.NotEmpty(links);
            // javascript:/file:/相对路径等一律移除导航，点击无反应
            Assert.All(links, l => Assert.Null(l.NavigateUri));
        });
    }

    [Fact]
    public void SelectedSourceSnippet_WebWithoutContent_ShowsActionableHint()
    {
        var vm = CreateVm(CreateFake());
        vm.SelectedSource = new SourceRef
        {
            SourceType = "web",
            Url = "https://filecenter.deltaww.com/manual.pdf",
            Snippet = null,
            ContentFetched = false,
        };

        // 正文没抓到 + 摘要为空：诚实提示并引导打开原文，而不是展示像链接一样的垃圾
        Assert.Contains("未抓到该网页正文", vm.SelectedSourceSnippet);
        Assert.Contains("在浏览器中打开", vm.SelectedSourceSnippet);
    }

    [Fact]
    public void SelectedSourceSnippet_WebWithContent_ShowsSnippet()
    {
        var vm = CreateVm(CreateFake());
        vm.SelectedSource = new SourceRef
        {
            SourceType = "web",
            Url = "https://example.com/asda-b3",
            Snippet = "本文深入解析台达 ASDA-B3 伺服驱动器核心参数",
            ContentFetched = true,
        };

        Assert.Equal("本文深入解析台达 ASDA-B3 伺服驱动器核心参数", vm.SelectedSourceSnippet);
    }

    [Fact]
    public async Task SendAsync_CollectsThinkingSteps_AndTokenStats()
    {
        var fake = CreateFake();
        fake.OnChatStreamWithStatus = (req, onToken, onDone, onStatus, onThinking, _) =>
        {
            onStatus?.Invoke("正在检索知识库...");
            onStatus?.Invoke("正在联网搜索与筛选资料...");
            onStatus?.Invoke("正在生成回答...");
            onThinking?.Invoke("用户想了解 B3 规格");
            onThinking?.Invoke("需要核对官方手册");
            onToken("答");
            onToken("案");
            var res = new ChatStreamResult
            {
                ChatId = "c1",
                Model = "deepseek-chat",
                Provider = "openai",
                TotalChunks = 3,
                ElapsedMs = 5000,
                Sources = new List<SourceRef>
                {
                    new() { Index = 1, Source = "https://example.com/a", SourceType = "web", Url = "https://example.com/a", ContentFetched = true },
                    new() { Index = 2, Source = "https://example.com/b", SourceType = "web", Url = "https://example.com/b", ContentFetched = false },
                    new() { Index = 3, Source = "doc.pdf", Format = "pdf", Page = 5 },
                },
            };
            onDone(res);
            return Task.FromResult(res);
        };
        var vm = CreateVm(fake);

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        var msg = vm.Messages.Last(m => m.Role == "assistant");
        // 思考过程步骤按序收集（去重后写入）
        Assert.Equal(new[] { "正在检索知识库...", "正在联网搜索与筛选资料...", "正在生成回答..." }, msg.ThinkingSteps);
        Assert.True(msg.HasThinkingSteps);
        Assert.Equal("用时 5.0 秒", msg.ThinkingDurationText);
        // 推理链增量累积为 ThinkingText（增量间自动补换行，防中英推理链粘连）
        Assert.Equal("用户想了解 B3 规格\n需要核对官方手册", msg.ThinkingText);
        Assert.True(msg.HasThinkingText);
        // token 统计与搜索摘要
        Assert.Equal(2, msg.TokenCount);
        Assert.Equal("搜索到 2 个网页 · 浏览 1 个页面", msg.SearchSummaryText);
        Assert.Contains("2 帧", msg.TokenStatText);
        Assert.Contains("3 个来源", msg.TokenStatText);
        Assert.Contains("5000ms", msg.TokenStatText);
    }

    [Fact]
    public async Task SendAsync_StreamStatusThenApiError_FinalizesThinkingSteps()
    {
        // 回归：流式状态发出后 API 报错，末尾「正在生成回答...」不得悬挂，
        // 阶段胶囊（ShowStatus）与等待首字状态必须关闭（错误提示旁不再显示进行中）。
        var fake = CreateFake();
        fake.OnChatStreamWithStatus = (req, onToken, onDone, onStatus, onThinking, _) =>
        {
            onStatus?.Invoke("正在检索知识库...");
            onStatus?.Invoke("正在生成回答...");
            throw new ApiException("RAG_ERROR", "回答生成中断：Error code: 404 - Function not found for account");
        };
        var vm = CreateVm(fake);

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        var msg = vm.Messages.Last(m => m.Role == "assistant");
        // 末尾「正在…」步骤被替换为「✖」失败标记（带底层原因摘要）
        Assert.Equal(2, msg.ThinkingSteps.Count);
        Assert.Equal("正在检索知识库...", msg.ThinkingSteps[0]);
        Assert.StartsWith("✖ 回答生成失败", msg.ThinkingSteps[1]);
        Assert.Contains("404", msg.ThinkingSteps[1]);
        // 阶段胶囊与等待首字状态均已关闭
        Assert.False(msg.ShowStatus);
        Assert.False(msg.IsWaitingForFirstToken);
        Assert.False(msg.IsLoading);
        // 正文仍展示错误提示
        Assert.Contains("API 错误", msg.Content);
        Assert.Contains("404", msg.Content);
    }

    [Fact]
    public async Task SendAsync_StopDuringThinking_FinalizesThinkingSteps()
    {
        // 停止生成路径：末尾「正在…」替换为「✖ 已停止生成」，同样不悬挂
        var fake = CreateFake();
        var vm = CreateVm(fake);
        fake.OnChatStreamWithStatus = async (req, onToken, onDone, onStatus, onThinking, ct) =>
        {
            onStatus?.Invoke("正在生成回答...");
            // 挂起直到用户取消（模拟首字未到达时点停止）
            await Task.Delay(Timeout.InfiniteTimeSpan, ct);
            return new ChatStreamResult { ChatId = "c-stop" };
        };

        vm.InputText = "问题";
        var sendTask = vm.SendCommand.ExecuteAsync(null);
        vm.StopCommand.Execute(null);
        await sendTask;

        var msg = vm.Messages.Last(m => m.Role == "assistant");
        Assert.EndsWith("✖ 已停止生成", msg.ThinkingSteps.Last());
        Assert.False(msg.ShowStatus);
    }

    [Fact]
    public async Task SendAsync_StreamEndsWithoutDoneFrame_FinalizesThinkingSteps()
    {
        // 回归：SSE 流被对端优雅关闭但没发 done 终帧（无异常抛出）时，
        // 「思考中」头部与「正在生成回答…」胶囊同样必须收尾，不得永久悬挂。
        var fake = CreateFake();
        fake.OnChatStreamWithStatus = (req, onToken, onDone, onStatus, onThinking, _) =>
        {
            onStatus?.Invoke("正在生成回答...");
            // 不调用 onToken / onDone，直接返回（模拟后端中断流）
            return Task.FromResult(new ChatStreamResult { ChatId = "c-nodone" });
        };
        var vm = CreateVm(fake);

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        var msg = vm.Messages.Last(m => m.Role == "assistant");
        Assert.EndsWith("✖ 回答生成失败：连接中断，未收到完成帧", msg.ThinkingSteps.Last());
        Assert.False(msg.ShowStatus);
        Assert.False(msg.IsLoading);
        Assert.False(msg.IsThinkingInProgress);
        // 新行为：流中断时提供详细的错误信息和重试引导，而非简单的"（无内容返回）"
        Assert.Contains("回答生成失败", msg.Content);
        Assert.Contains("连接中断", msg.Content);
        Assert.Contains("重新生成", msg.Content);
        // 恢复输入框
        Assert.Equal("问题", vm.InputText);
    }

    [Fact]
    public void WebSearchMode_LoadsFromSettings_AndPersistsOnToggle()
    {
        var settings = new AppSettings { EnableWebSearch = true };
        var vm = new ChatViewModel(CreateFake(), null, settings);

        // 旧配置仅 EnableWebSearch=true → 迁移为普通搜索
        Assert.Equal("normal", vm.SelectedWebSearchMode.Key);
        Assert.True(vm.IsWebSearchEnabled);

        // 选关闭 → 写回 AppSettings 并落盘
        vm.SelectedWebSearchMode = DocMind.Models.WebSearchModeChoice.Off;
        Assert.Equal("off", settings.WebSearchMode);
        Assert.False(settings.EnableWebSearch);
        Assert.False(vm.IsWebSearchEnabled);

        // 选深度搜索 → 写回并落盘
        vm.SelectedWebSearchMode = DocMind.Models.WebSearchModeChoice.Deep;
        Assert.Equal("deep", settings.WebSearchMode);
        Assert.True(settings.EnableWebSearch);
        Assert.True(vm.IsWebSearchEnabled);

        // 旧布尔 setter：true 应至少升到普通搜索
        vm.SelectedWebSearchMode = DocMind.Models.WebSearchModeChoice.Off;
        vm.IsWebSearchEnabled = true;
        Assert.Equal("normal", settings.WebSearchMode);
        Assert.True(settings.EnableWebSearch);
    }

    // ======================================================================
    // 知识库勾选持久化
    // ======================================================================

    [Fact]
    public void Collections_SelectionPersistsToSettings_AndRestoredOnRestart()
    {
        var fake = CreateFake();
        fake.OnGetStats = (_, _) => Task.FromResult(new Stats
        {
            TotalDocuments = 0,
            TotalChunks = 0,
            Collections = new Dictionary<string, int[]> { { "default", [0, 0, 0] }, { "docs-a", [0, 0, 0] } },
        });
        var settings = new AppSettings();

        var vm = new ChatViewModel(fake, null, settings);
        // 构造时首次加载已同步完成（Fake 返回已完成 Task）→ 勾选 docs-a 即落盘
        vm.Collections.First(c => c.Name == "docs-a").IsSelected = true;

        Assert.Contains("docs-a", settings.LastChatCollections);
        Assert.Contains("default", settings.LastChatCollections);

        // 模拟重启：同一份 AppSettings 新建 VM → 勾选恢复
        var vm2 = new ChatViewModel(fake, null, settings);
        Assert.True(vm2.Collections.First(c => c.Name == "docs-a").IsSelected);
        Assert.True(vm2.Collections.First(c => c.Name == "default").IsSelected);
    }

    [Fact]
    public void Collections_SavedCollectionDeleted_FallsBackToDefault()
    {
        var fake = CreateFake();
        fake.OnGetStats = (_, _) => Task.FromResult(new Stats
        {
            TotalDocuments = 0,
            TotalChunks = 0,
            Collections = new Dictionary<string, int[]> { { "default", [0, 0, 0] }, { "docs-a", [0, 0, 0] } },
        });
        var settings = new AppSettings();

        var vm = new ChatViewModel(fake, null, settings);
        vm.Collections.First(c => c.Name == "docs-a").IsSelected = true;
        Assert.Contains("docs-a", settings.LastChatCollections);

        // 重启时 docs-a 已在后端删除（Fake 只返回 default）→ 恢复匹配不到 → 回退默认勾选 default
        fake.OnGetStats = (_, _) => Task.FromResult(new Stats
        {
            TotalDocuments = 0,
            TotalChunks = 0,
            Collections = new Dictionary<string, int[]> { { "default", [0, 0, 0] } },
        });
        var vm2 = new ChatViewModel(fake, null, settings);
        Assert.True(vm2.Collections.First(c => c.Name == "default").IsSelected);
        Assert.False(vm2.Collections.Any(c => c.Name == "docs-a"));
    }

    // ======================================================================
    // LLM 未配置事前引导
    // ======================================================================

    [Fact]
    public async Task SendAsync_WithoutLlmConfigured_InterceptsWithGuidance()
    {
        var fake = CreateFake();
        // LlmProvider 默认 none 且未选服务商模型 → 未配置；Fake 的 GetConfigAsync 未设置会抛，
        // 但 SeedModelFromConfigAsync 内部吞掉异常，不影响拦截判断
        var vm = new ChatViewModel(fake, null, new AppSettings());
        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.False(vm.IsLlmConfigured);
        Assert.Empty(vm.Messages); // 事前拦截：不产生任何消息、不发请求
        Assert.Contains("尚未配置大模型", vm.StatusMessage);
    }

    [Fact]
    public void EmptyGuideText_BranchesOnLlmConfigured()
    {
        var unconfigured = new ChatViewModel(CreateFake(), null, new AppSettings());
        Assert.Contains("尚未配置大模型", unconfigured.EmptyGuideText);

        var configured = CreateVm(CreateFake());
        Assert.Contains("开始与知识库对话", configured.EmptyGuideText);
    }

    // ======================================================================
    // 会话消息数展示
    // ======================================================================

    [Fact]
    public async Task MessagesCountText_UpdatesAsMessagesArrive()
    {
        var fake = CreateFake();
        fake.OnChatStreamWithStatus = (req, onToken, onDone, onStatus, onThinking, _) =>
        {
            onToken("回答内容");
            var res = new ChatStreamResult
            {
                ChatId = "c1", Model = "m", Provider = "p", ElapsedMs = 100,
                Sources = new List<SourceRef>(),
            };
            onDone(res);
            return Task.FromResult(res);
        };
        var vm = CreateVm(fake);
        Assert.Equal(string.Empty, vm.MessagesCountText); // 无消息时隐藏

        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.Equal(2, vm.Messages.Count); // 用户消息 + 助手消息
        Assert.Equal("💬 2 条消息", vm.MessagesCountText);
    }

    [Fact]
    public async Task SendAsync_DetailStatusReplacesPendingStep_AndKeepsThinkingExpanded()
    {
        var fake = CreateFake();
        fake.OnChatStreamWithStatus = (req, onToken, onDone, onStatus, onThinking, _) =>
        {
            onStatus?.Invoke("正在查询实体关系...");
            onStatus?.Invoke("✔ 实体关系：找到 2 条关联");
            onStatus?.Invoke("正在检索知识库...");
            onStatus?.Invoke("✔ 检索知识库：命中 5 个分块");
            onStatus?.Invoke("正在生成回答...");
            onToken("答");
            var res = new ChatStreamResult
            {
                ChatId = "c1", Model = "m", Provider = "p", ElapsedMs = 3000,
                Sources = new List<SourceRef>(),
            };
            onDone(res);
            return Task.FromResult(res);
        };
        var vm = CreateVm(fake);
        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        var msg = vm.Messages.Last(m => m.Role == "assistant");
        // 「✔ 详情」替换上一条「正在…」步骤，每一步紧凑且带详情
        Assert.Equal(new[]
        {
            "✔ 实体关系：找到 2 条关联",
            "✔ 检索知识库：命中 5 个分块",
            "正在生成回答...",
        }, msg.ThinkingSteps);
        // 答完自动收起思考区（2026-09-13 产品决定：流式期间展开看进度，答完收起，「已思考」可再展开）
        Assert.False(msg.IsThinkingExpanded);
        Assert.True(msg.HasThinking);
    }

    [Fact]
    public void SourceMarkers_ConvertedForValidIndex_AndClickFiresEvent()
    {
        RunOnSta(() =>
        {
            var msg = new ChatMessage { Role = "assistant" };
            msg.Sources = new List<SourceRef>
            {
                new() { Index = 1, Source = "https://example.com/a", SourceType = "web", Url = "https://example.com/a" },
            };
            int clicked = 0;
            msg.SourceMarkerRequested += index => clicked = index;

            msg.Content = "官方资料见[1]；越界的[5]不转换。";

            Assert.NotNull(msg.RenderedDocument);
            var links = FindHyperlinks(msg.RenderedDocument!).ToList();
            // 仅 [1] 被转换为角标（无 NavigateUri 的小号链接）；[5] 越界保留原文
            var markers = links.Where(l => l.NavigateUri is null).ToList();
            Assert.Single(markers);
            var markerText = string.Concat(markers[0].Inlines.OfType<Run>().Select(r => r.Text));
            Assert.Contains("1", markerText);
            Assert.Contains("🌐", markerText);

            // 事件接线：角标点击 → SourceMarkerRequested(1)
            msg.NotifySourceMarker(1);
            Assert.Equal(1, clicked);
            // 点击角标：展开来源列表 + 临时高亮对应卡片
            Assert.True(msg.IsSourcesExpanded);
            Assert.Equal(1, msg.HighlightedSourceIndex);
        });
    }

    [Fact]
    public void SourceMarkers_SkipCodeBlock()
    {
        RunOnSta(() =>
        {
            var msg = new ChatMessage { Role = "assistant" };
            msg.Sources = new List<SourceRef>
            {
                new() { Index = 1, Source = "https://example.com/a", SourceType = "web", Url = "https://example.com/a" },
            };

            // 代码块里的 [1] 不应被当作引用角标（前后粘着字符 + 代码块跳过）
            msg.Content = "代码示例：\n```csharp\nvar x = arr[1];\n```";

            Assert.NotNull(msg.RenderedDocument);
            var markers = FindHyperlinks(msg.RenderedDocument!).Where(l => l.NavigateUri is null).ToList();
            Assert.Empty(markers);
        });
    }

    [Fact]
    public void SourceMarkerClick_ExpandsCollapsedSourcesList()
    {
        var msg = new ChatMessage { Role = "assistant" };
        msg.Sources = new List<SourceRef>
        {
            new() { Index = 2, SourceType = "local", Source = "doc.pdf", Page = 3 },
        };
        msg.IsSourcesExpanded = false;
        Assert.False(msg.ShowSourcesList);

        msg.NotifySourceMarker(2);

        Assert.True(msg.IsSourcesExpanded);
        Assert.True(msg.ShowSourcesList);
        Assert.Equal(2, msg.HighlightedSourceIndex);
    }

    /// <summary>在专用 STA 线程上执行断言（Markdig.Wpf 渲染需要 STA）。</summary>
    private static void RunOnSta(Action action)
    {
        Exception? error = null;
        var thread = new Thread(() =>
        {
            try
            {
                action();
            }
            catch (Exception ex)
            {
                error = ex;
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (error is not null)
        {
            throw new Xunit.Sdk.XunitException($"STA 线程断言失败: {error}", error);
        }
    }

    private static IEnumerable<Hyperlink> FindHyperlinks(FlowDocument doc)
        => doc.Blocks.SelectMany(FindHyperlinks);

    private static IEnumerable<Hyperlink> FindHyperlinks(Block block) => block switch
    {
        Paragraph p => p.Inlines.SelectMany(FindHyperlinks),
        List l => l.ListItems.SelectMany(li => li.Blocks.SelectMany(FindHyperlinks)),
        Table t => t.RowGroups.SelectMany(rg => rg.Rows)
                              .SelectMany(r => r.Cells)
                              .SelectMany(c => c.Blocks)
                              .SelectMany(FindHyperlinks),
        Section s => s.Blocks.SelectMany(FindHyperlinks),
        _ => Enumerable.Empty<Hyperlink>(),
    };

    private static IEnumerable<Hyperlink> FindHyperlinks(Inline inline) => inline switch
    {
        Hyperlink h => new[] { h }.Concat(h.Inlines.SelectMany(FindHyperlinks)),
        Span sp => sp.Inlines.SelectMany(FindHyperlinks),
        _ => Enumerable.Empty<Hyperlink>(),
    };

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

        vm.OpenSourceCommand.Execute(src);

        Assert.True(vm.IsSourceDrawerOpen);
        Assert.NotNull(vm.SelectedSource);
        Assert.Equal(src.Index, vm.SelectedSource.Index);
        Assert.Equal(src.Source, vm.SelectedSource.Source);
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

    // ======================================================================
    // 智能下一步行动建议 (Follow-up Actions)
    // ======================================================================

    [Fact]
    public void ChatMessage_ParsesFollowUpActions_AndCleansRenderedContent()
    {
        var msg = new ChatMessage
        {
            Role = "assistant",
            Content = "这是技术分析正文。\n\n[ACTIONS: [\"👉 生成排期计划表\", \"👉 评估对现场工况的影响\"]]"
        };

        Assert.True(msg.HasFollowUpActions);
        Assert.Equal(2, msg.FollowUpActions.Count);
        Assert.Equal("👉 生成排期计划表", msg.FollowUpActions[0]);
        Assert.Equal("👉 评估对现场工况的影响", msg.FollowUpActions[1]);
        Assert.NotNull(msg.RenderedDocument);
    }

    [Fact]
    public async Task ExecuteActionCommand_TriggersNewQuery()
    {
        var fake = CreateFake();
        fake.OnChat = (req, _) => Task.FromResult(MakeResponse($"收到追问: {req.Query}"));
        var vm = CreateVm(fake);

        await vm.ExecuteActionCommand.ExecuteAsync("👉 评估现场工况");

        Assert.Equal(2, vm.Messages.Count);
        Assert.Equal("评估现场工况", vm.Messages[0].Content);
        Assert.Contains("收到追问: 评估现场工况", vm.Messages[1].Content);
    }

    // ======================================================================
    // 办公角色人设与场景快捷指令
    // ======================================================================

    [Fact]
    public void ChatViewModel_InitializesDefaultPersona_AndSupportsSwitching()
    {
        var fake = CreateFake();
        var vm = CreateVm(fake);

        Assert.NotEmpty(vm.AvailablePersonas);
        Assert.Equal("office", vm.SelectedPersona.Id);

        var architect = vm.AvailablePersonas.First(p => p.Id == "architect");
        vm.SelectedPersona = architect;
        Assert.Equal("architect", vm.SelectedPersona.Id);
    }

    [Fact]
    public void InsertPromptTemplateCommand_InjectsTemplatePrefix()
    {
        var fake = CreateFake();
        var vm = CreateVm(fake);

        vm.InsertPromptTemplateCommand.Execute("summary");
        Assert.Contains("Action Items", vm.InputText);

        vm.InputText = "这是原文";
        vm.InsertPromptTemplateCommand.Execute("table");
        Assert.StartsWith("请以结构化 Markdown 表格形式", vm.InputText);
        Assert.EndsWith("这是原文", vm.InputText);
    }

    [Fact]
    public async Task SendAsync_IncludesSelectedPersonaInChatRequest()
    {
        var fake = CreateFake();
        ChatRequest? capturedReq = null;
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            capturedReq = req;
            var res = new ChatStreamResult { Model = "m", Provider = "p" };
            onDone(res);
            return Task.FromResult(res);
        };
        var vm = CreateVm(fake);
        vm.SelectedPersona = vm.AvailablePersonas.First(p => p.Id == "engineer");
        vm.InputText = "写一个单测";

        await vm.SendCommand.ExecuteAsync(null);

        Assert.NotNull(capturedReq);
        Assert.Equal("engineer", capturedReq.Persona);
    }

    [Fact]
    public async Task SendAsync_NaturalLanguagePptRequest_AutoRoutesToPptPersona()
    {
        // 用户仅用自然语言说「做个 PPT」，未显式选创作人设（默认 office）。
        // 后端应自动路由到 ppt 人设并在 done 帧回传，前端静默同步人设下拉框，
        // 助手消息解析出结构化创作物（:::artifact），供前端自动导出 PPTX。
        var fake = CreateFake();
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            Assert.Equal("office", req.Persona); // 前端仍按默认 office 发出
            onToken(":::artifact type=\"pptx\" title=\"AI 汇报\" theme=\"tech_blue\"\n" +
                    "---\n# 人工智能演示文稿\n## 副标题\n:::\n");
            var res = new ChatStreamResult
            {
                Model = "m",
                Provider = "p",
                Persona = "ppt", // 后端自动切换后的实际生效人设
                TotalChunks = 0,
                ElapsedMs = 10,
                Sources = [],
            };
            onDone(res);
            return Task.FromResult(res);
        };

        var vm = CreateVm(fake);
        Assert.Equal("office", vm.SelectedPersona.Id); // 默认人设
        vm.InputText = "帮我做个关于人工智能的PPT";

        await vm.SendCommand.ExecuteAsync(null);

        // 人设下拉框被后端回传的 ppt 自动同步
        Assert.Equal("ppt", vm.SelectedPersona.Id);
        // 助手消息应解析出结构化创作物（:::artifact）
        var assistantMsg = vm.Messages.LastOrDefault(m => m.Role == "assistant");
        Assert.NotNull(assistantMsg);
        Assert.True(assistantMsg.HasArtifact);
        Assert.NotNull(assistantMsg.Artifact);
        Assert.True(assistantMsg.Artifact.IsPpt);
    }

    [Fact]
    public async Task SendAsync_NaturalLanguageWordRequest_AutoRoutesToDocPersona()
    {
        // 「用 word 写个文档」未显式选创作人设（默认 office），后端自动路由到 doc，
        // 前端静默同步人设下拉框，助手消息解析出 :::artifact 交付物。
        var fake = CreateFake();
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            Assert.Equal("office", req.Persona);
            onToken(":::artifact type=\"docx\" title=\"项目立项书\"\n" +
                    "---\n# 项目背景\n正文内容\n:::\n");
            var res = new ChatStreamResult
            {
                Model = "m",
                Provider = "p",
                Persona = "doc",
                TotalChunks = 0,
                ElapsedMs = 10,
                Sources = [],
            };
            onDone(res);
            return Task.FromResult(res);
        };

        var vm = CreateVm(fake);
        Assert.Equal("office", vm.SelectedPersona.Id);
        vm.InputText = "用 word 写个项目立项书";

        await vm.SendCommand.ExecuteAsync(null);

        Assert.Equal("doc", vm.SelectedPersona.Id);
        var assistantMsg = vm.Messages.LastOrDefault(m => m.Role == "assistant");
        Assert.NotNull(assistantMsg);
        Assert.True(assistantMsg.HasArtifact);
        Assert.NotNull(assistantMsg.Artifact);
    }

    [Fact]
    public async Task SendAsync_NaturalLanguageExcelRequest_AutoRoutesToTablePersona()
    {
        // 「做个 excel 报表」未显式选创作人设（默认 office），后端自动路由到 table，
        // 前端静默同步人设下拉框，助手消息解析出 :::artifact 交付物。
        var fake = CreateFake();
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            Assert.Equal("office", req.Persona);
            onToken(":::artifact type=\"xlsx\" title=\"季度对比表\"\n" +
                    "---\n| 维度 | A | B |\n|---|---|---|\n| 营收 | 100 | 120 |\n:::\n");
            var res = new ChatStreamResult
            {
                Model = "m",
                Provider = "p",
                Persona = "table",
                TotalChunks = 0,
                ElapsedMs = 10,
                Sources = [],
            };
            onDone(res);
            return Task.FromResult(res);
        };

        var vm = CreateVm(fake);
        Assert.Equal("office", vm.SelectedPersona.Id);
        vm.InputText = "帮我做个 excel 季度对比表";

        await vm.SendCommand.ExecuteAsync(null);

        Assert.Equal("table", vm.SelectedPersona.Id);
        var assistantMsg = vm.Messages.LastOrDefault(m => m.Role == "assistant");
        Assert.NotNull(assistantMsg);
        Assert.True(assistantMsg.HasArtifact);
        Assert.NotNull(assistantMsg.Artifact);
    }

    // ======================================================================
    // 对话一键沉淀入库 (Knowledge Flywheel)
    // ======================================================================

    [Fact]
    public async Task IngestMessageCommand_CallsIngestTextApi_AndMarksMessageIngested()
    {
        var fake = CreateFake();
        IngestTextRequest? capturedReq = null;
        fake.OnIngestText = (req, _) =>
        {
            capturedReq = req;
            return Task.FromResult(new IngestResponse { TotalDocuments = 1, TotalChunks = 1 });
        };
        var vm = CreateVm(fake);

        var msg = new ChatMessage
        {
            Role = "assistant",
            Content = "## 📌 动平衡现场标定排错工序\n1. 检查零漂\n2. 校准增益\n\n[ACTIONS: [\"👉 建议\"]]"
        };
        vm.IngestMessageCommand.Execute(msg);
        Assert.True(vm.IsIngestDialogOpen);
        await vm.ConfirmIngestDialogCommand.ExecuteAsync(null);

        Assert.False(vm.IsIngestDialogOpen);
        Assert.False(msg.IsIngesting);
        Assert.True(msg.IsIngested);
        Assert.NotNull(capturedReq);
        Assert.Contains("动平衡现场标定排错工序", capturedReq.Title);
        Assert.DoesNotContain("[ACTIONS:", capturedReq.Text);
        Assert.Contains("检查零漂", capturedReq.Text);
    }

    // ======================================================================
    // 示例文档库一键极速体验与快捷提问
    // ======================================================================

    [Fact]
    public async Task IngestSampleKnowledgeCommand_IngestsSampleAndPreparesQuestion()
    {
        var fake = CreateFake();
        bool sampleIngested = false;
        fake.OnIngestSample = (col, _) =>
        {
            sampleIngested = true;
            return Task.FromResult(new SampleIngestResult
            {
                Ok = true,
                Status = "ingested",
                Title = "DocMind 快速上手与全景操作指南",
                Collection = col,
                ChunkCount = 1,
                ElapsedMs = 20
            });
        };

        var vm = CreateVm(fake);
        await vm.IngestSampleKnowledgeCommand.ExecuteAsync(null);

        Assert.True(sampleIngested);
        Assert.Contains("DocMind", vm.InputText);
        Assert.Contains("导入成功", vm.StatusMessage);
    }

    [Fact]
    public async Task QuickAskCommand_SetsInputAndSends()
    {
        var fake = CreateFake();
        ChatRequest? sentReq = null;
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            sentReq = req;
            var res = new ChatStreamResult { Model = "m", Provider = "p" };
            onDone(res);
            return Task.FromResult(res);
        };

        var vm = CreateVm(fake);
        await vm.QuickAskCommand.ExecuteAsync("DocMind 核心能力是什么？");

        Assert.NotNull(sentReq);
        Assert.Equal("DocMind 核心能力是什么？", sentReq.Query);
    }

    [Fact]
    public void AddAttachmentPaths_AddsItemsAndUpdatesHasPendingAttachments()
    {
        var fake = CreateFake();
        var vm = CreateVm(fake);

        // 创建临时测试文件
        var tempFile = Path.GetTempFileName();
        try
        {
            vm.AddAttachmentPaths([tempFile]);
            Assert.True(vm.HasPendingAttachments);
            Assert.Single(vm.PendingAttachments);
            Assert.True(vm.HasInput);

            // 移除附件
            vm.RemoveAttachmentCommand.Execute(vm.PendingAttachments[0]);
            Assert.False(vm.HasPendingAttachments);
            Assert.Empty(vm.PendingAttachments);
        }
        finally
        {
            if (File.Exists(tempFile)) File.Delete(tempFile);
        }
    }

    [Fact]
    public async Task SendAsync_WithAttachments_PassesAttachmentsToRequest()
    {
        var fake = CreateFake();
        ChatRequest? sentReq = null;
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            sentReq = req;
            var res = new ChatStreamResult { Model = "m", Provider = "p" };
            onDone(res);
            return Task.FromResult(res);
        };

        var vm = CreateVm(fake);
        var tempFile = Path.GetTempFileName();
        try
        {
            vm.AddAttachmentPaths([tempFile]);
            vm.InputText = "请分析附件";
            await vm.SendCommand.ExecuteAsync(null);

            Assert.NotNull(sentReq);
            Assert.NotNull(sentReq.Attachments);
            Assert.Single(sentReq.Attachments);
            Assert.Equal(tempFile, sentReq.Attachments[0]);
            // 发送后已自动清空待发列表
            Assert.False(vm.HasPendingAttachments);
        }
        finally
        {
            if (File.Exists(tempFile)) File.Delete(tempFile);
        }
    }

    // ======================================================================
    // 模型选择器：点选模型即切换服务商，发送携带 ProviderConfig
    // ======================================================================

    [Fact]
    public void Constructor_LoadsModelChoices_DefaultFirst_SelectsDefaultItem()
    {
        var fake = CreateFake();
        var active = new LlmProfile
        {
            Id = "p1",
            Name = "DeepSeek",
            Provider = "openai",
            Model = "deepseek-chat",
            Models = new List<string> { "deepseek-chat", "deepseek-reasoner" },
            ApiKey = "k1",
        };
        var settings = new AppSettings
        {
            LlmApiKey = "test-key",
            LlmProfiles = new List<LlmProfile>
            {
                active,
                new LlmProfile { Id = "p2", Name = "Ollama 本地", Provider = "ollama", Model = "llama3.2", Models = new List<string> { "llama3.2" }, ApiKey = "k2" },
            },
            ActiveProfileId = "p1",
        };

        var vm = new ChatViewModel(fake, null, settings);

        // 首项为「默认」伪，其后为各启用服务商的模型（「模型名（服务商名）」）
        Assert.Equal(1 + 2 + 1, vm.ModelChoices.Count);
        Assert.True(vm.ModelChoices[0].IsDefault);
        Assert.Null(vm.ModelChoices[0].Provider);
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "deepseek-chat（DeepSeek）");
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "deepseek-reasoner（DeepSeek）");
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "llama3.2（Ollama 本地）");
        // 无持久化记录时默认选中首项「默认」伪项（用设置页配置），而不是档案模型
        Assert.True(vm.SelectedModelChoice?.IsDefault);
        Assert.Null(vm.SelectedModelChoice?.Provider);
        Assert.Null(vm.SelectedModelChoice?.Model);
    }

    [Fact]
    public void Constructor_DefaultItem_ShowsRealDefaultModelName()
    {
        var fake = CreateFake();
        // 设置页配置了默认模型 → 首项显示「默认 · 模型名」（已配 Key 才显示默认项）
        var vm = new ChatViewModel(fake, null, new AppSettings { LlmModel = "qwen2.5:7b", LlmProvider = "ollama", LlmApiKey = "test-key" });
        Assert.Equal("默认 · qwen2.5:7b", vm.ModelChoices[0].DisplayName);
        Assert.True(vm.ModelChoices[0].IsDefault);
        Assert.Null(vm.ModelChoices[0].Model);

        // 未配置任何 Key → 不显示默认项（空态引导由 EmptyGuideText 接管）
        var vm2 = new ChatViewModel(fake, null, new AppSettings());
        Assert.Empty(vm2.ModelChoices);
    }

    [Fact]
    public void SelectedModelChoice_IsPersistedAndRestoredOnNewViewModel()
    {
        var fake = CreateFake();
        var settings = new AppSettings
        {
            LlmModel = "qwen2.5:7b",
            LlmProvider = "ollama",
            LlmApiKey = "test-key",
            LlmProfiles = new List<LlmProfile>
            {
                new() { Id = "p1", Name = "DeepSeek", Provider = "openai", Model = "deepseek-chat", Models = new List<string> { "deepseek-chat" }, ApiKey = "k1" },
            },
        };
        var vm1 = new ChatViewModel(fake, null, settings);
        // 点选某服务商的模型 → 落盘（档案 Id + 模型名）
        vm1.SelectedModelChoice = vm1.ModelChoices.First(c => c.Model == "deepseek-chat" && c.Provider?.Id == "p1");
        Assert.Equal("p1", settings.LastChatProfileId);
        Assert.Equal("deepseek-chat", settings.LastChatModel);

        // 模拟重启：新 VM 用同一 AppSettings → 还原上次对话页选择
        var vm2 = new ChatViewModel(fake, null, settings);
        Assert.Equal("deepseek-chat", vm2.SelectedModelChoice?.Model);
        Assert.Equal("p1", vm2.SelectedModelChoice?.Provider?.Id);
    }

    [Fact]
    public void SelectedModelChoice_DefaultGroupModel_PersistedAndRestored()
    {
        var fake = CreateFake();
        var settings = new AppSettings { LlmModel = "qwen2.5:7b", LlmProvider = "ollama", LlmApiKey = "test-key" };
        var vm1 = new ChatViewModel(fake, null, settings);
        // 点选「默认提供商分组」的模型（Provider 为 null、非默认伪项）
        vm1.SelectedModelChoice = vm1.ModelChoices.First(c => c.Provider is null && !c.IsDefault && c.Model == "qwen2.5:7b");
        Assert.Null(settings.LastChatProfileId);
        Assert.Equal("qwen2.5:7b", settings.LastChatModel);

        // 模拟重启且默认模型已更换（分组种子不再是旧选择）→ 仍应还原上次选择
        settings.LlmModel = "new-default-model";
        var vm2 = new ChatViewModel(fake, null, settings);
        Assert.Equal("qwen2.5:7b", vm2.SelectedModelChoice?.Model);
        Assert.Null(vm2.SelectedModelChoice?.Provider);
    }

    [Fact]
    public async Task RefreshModels_KeepsPersistedGroupModelInPool()
    {
        var fake = CreateFake();
        // 拉取结果不含持久化的分组模型（如换服务商后列表变化）
        fake.OnLlmModels = (_, _) => Task.FromResult(new LlmModelsResult
        {
            Ok = true,
            Provider = "ollama",
            Models = new[] { "llama3.2:latest" },
        });

        var settings = new AppSettings
        {
            LlmModel = "qwen2.5:7b",
            LlmProvider = "ollama",
            LlmApiKey = "test-key",
            LastChatModel = "deepseek-r1:8b",
        };
        var vm = new ChatViewModel(fake, null, settings);

        await vm.RefreshModelsCommand.ExecuteAsync(null);

        // 持久化的分组模型仍在候选池（不因拉取结果丢失）
        Assert.Contains(vm.ModelChoices, c => c.Provider is null && !c.IsDefault && c.Model == "deepseek-r1:8b");
    }

    [Fact]
    public void RebuildModelChoices_SameEndpointProfile_DeduplicatesDefaultGroupModels()
    {
        var fake = CreateFake();
        // 设置页默认配置与启用档案指向同一端点（BaseUrl 归一化比较，结尾斜杠差异不影响判定）
        var settings = new AppSettings
        {
            LlmModel = "01-ai/yi-large",
            LlmProvider = "openai",
            LlmApiKey = "test-key",
            LlmBaseUrl = "https://integrate.api.nvidia.com/v1",
            LlmProfiles = new List<LlmProfile>
            {
                new()
                {
                    Id = "p1",
                    Name = "nvidia",
                    Provider = "openai",
                    BaseUrl = "https://integrate.api.nvidia.com/v1/",
                    Model = "01-ai/yi-large",
                    Models = new List<string> { "01-ai/yi-large", "adept/fuyu-8b" },
                    ApiKey = "k1",
                },
            },
        };
        var vm = new ChatViewModel(fake, null, settings);

        // 默认分组不再以裸名重复列出同端点档案已有的模型；档案条目正常保留
        Assert.DoesNotContain(vm.ModelChoices, c => c.Provider is null && !c.IsDefault);
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "01-ai/yi-large（nvidia）");
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "adept/fuyu-8b（nvidia）");
        Assert.Equal(3, vm.ModelChoices.Count); // 默认伪项 + 档案两个模型
    }

    [Fact]
    public void RebuildModelChoices_DifferentEndpointProfile_KeepsDefaultGroupModels()
    {
        var fake = CreateFake();
        // 默认配置走官方端点（无 BaseUrl），档案走中转端点：同名模型分属不同端点，不去重
        var settings = new AppSettings
        {
            LlmModel = "gpt-4o",
            LlmProvider = "openai",
            LlmApiKey = "test-key",
            LlmProfiles = new List<LlmProfile>
            {
                new()
                {
                    Id = "p1",
                    Name = "中转",
                    Provider = "openai",
                    BaseUrl = "https://relay.example.com/v1",
                    Model = "gpt-4o",
                    Models = new List<string> { "gpt-4o" },
                    ApiKey = "k1",
                },
            },
        };
        var vm = new ChatViewModel(fake, null, settings);

        Assert.Contains(vm.ModelChoices, c => c.Provider is null && !c.IsDefault && c.Model == "gpt-4o");
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "gpt-4o（中转）");
        Assert.Equal(3, vm.ModelChoices.Count);
    }

    [Fact]
    public void SelectedModelChoice_DedupedDefaultGroupEntry_RestoresToSameEndpointTwin()
    {
        var fake = CreateFake();
        var settings = new AppSettings
        {
            LlmModel = "01-ai/yi-large",
            LlmProvider = "openai",
            LlmApiKey = "test-key",
            LlmBaseUrl = "https://integrate.api.nvidia.com/v1",
            LlmProfiles = new List<LlmProfile>
            {
                new()
                {
                    Id = "p1",
                    Name = "nvidia",
                    Provider = "openai",
                    BaseUrl = "https://integrate.api.nvidia.com/v1",
                    Model = "01-ai/yi-large",
                    Models = new List<string> { "01-ai/yi-large" },
                    ApiKey = "k1",
                },
            },
            // 旧版本遗留的持久化：选中默认分组裸项（无档案 Id）
            LastChatProfileId = null,
            LastChatModel = "01-ai/yi-large",
        };
        var vm = new ChatViewModel(fake, null, settings);

        // 裸项已被去重 → 还原到同端点档案的「01-ai/yi-large（nvidia）」孪生条目，而不是退回默认项
        Assert.Equal("p1", vm.SelectedModelChoice?.Provider?.Id);
        Assert.Equal("01-ai/yi-large", vm.SelectedModelChoice?.Model);
    }

    // ======================================================================
    // PPT 定制偏好（创作前置征询 → 编译为结构化提示词）
    // ======================================================================

    [Fact]
    public void ConfirmPptPrefs_CompilesAllPreferencesIntoPrompt()
    {
        var vm = CreateVm(CreateFake());

        vm.PptPurpose = "客户提案 / 商务推介";
        vm.PptLength = "精简 6-8 页";
        vm.PptPrefTheme = vm.AvailableThemes.First(t => t.Id == "emerald_green");
        vm.PptMustInclude = "必须有一页讲落地路径";

        vm.ConfirmPptPrefsCommand.Execute(null);

        var prompt = vm.InputText;
        // 用户填的每一项创作意图都必须落到提示词里，否则等于白征询
        Assert.Contains("客户提案 / 商务推介", prompt);
        Assert.Contains("精简 6-8 页", prompt);
        Assert.Contains("必须有一页讲落地路径", prompt);
        // 主题 id 要原样带进 artifact 头，前端才能同步配色
        Assert.Contains("emerald_green", prompt);
        // 格式约定必须与前端 ArtifactRegex / 幻灯片切片解析规则对齐
        Assert.Contains(":::artifact type=\"pptx\"", prompt);

        Assert.False(vm.IsPptPrefsOpen);
    }

    [Fact]
    public void ConfirmPptPrefs_BlankMustInclude_OmitsThatRequirement()
    {
        var vm = CreateVm(CreateFake());
        vm.PptMustInclude = "   ";

        vm.ConfirmPptPrefsCommand.Execute(null);

        Assert.DoesNotContain("必须包含以下内容要点", vm.InputText);
    }

    [Fact]
    public void CancelPptPrefs_ClosesDialogWithoutTouchingInput()
    {
        var vm = CreateVm(CreateFake());
        vm.InputText = "原有输入";
        vm.IsPptPrefsOpen = true;

        vm.CancelPptPrefsCommand.Execute(null);

        Assert.False(vm.IsPptPrefsOpen);
        Assert.Equal("原有输入", vm.InputText);
    }

    [Fact]
    public void SelectedModelChoice_DefaultItem_PersistedAndRestored()
    {
        var fake = CreateFake();
        var settings = new AppSettings
        {
            LlmModel = "qwen2.5:7b",
            LlmProvider = "ollama",
            LlmApiKey = "test-key",
            LlmProfiles = new List<LlmProfile>
            {
                new() { Id = "p1", Name = "DeepSeek", Provider = "openai", Model = "deepseek-chat", Models = new List<string> { "deepseek-chat" }, ApiKey = "k1" },
            },
        };
        var vm1 = new ChatViewModel(fake, null, settings);
        // 先选某服务商模型，再切回「默认」项 → 两项皆空
        vm1.SelectedModelChoice = vm1.ModelChoices.First(c => c.Model == "deepseek-chat" && c.Provider?.Id == "p1");
        vm1.SelectedModelChoice = vm1.ModelChoices.First(c => c.IsDefault);
        Assert.Null(settings.LastChatProfileId);
        Assert.Null(settings.LastChatModel);

        // 模拟重启：还原为「默认」项
        var vm2 = new ChatViewModel(fake, null, settings);
        Assert.True(vm2.SelectedModelChoice?.IsDefault);
        Assert.Equal(ChatViewModel.DefaultModelLabel, vm2.SelectedModel);
    }

    [Fact]
    public void DisabledProvider_NotIncludedInModelChoices()
    {
        var fake = CreateFake();
        var disabled = new LlmProfile
        {
            Id = "p1",
            Name = "DeepSeek",
            Provider = "openai",
            IsEnabled = false,
            Models = new List<string> { "deepseek-chat" },
        };
        var settings = new AppSettings { LlmApiKey = "test-key", LlmProfiles = new List<LlmProfile> { disabled } };

        var vm = new ChatViewModel(fake, null, settings);

        // 停用的服务商不出现在点选列表（配置保留）
        Assert.Single(vm.ModelChoices);
        Assert.True(vm.ModelChoices[0].IsDefault);
    }

    [Fact]
    public void RebuildModelChoices_ReflectsLatestProviders()
    {
        var fake = CreateFake();
        var settings = new AppSettings { LlmApiKey = "test-key" };
        var vm = new ChatViewModel(fake, null, settings);
        Assert.Single(vm.ModelChoices); // 仅默认项

        // 模拟设置页新增服务商（事件驱动 RebuildModelChoices）
        settings.LlmProfiles = new List<LlmProfile>
        {
            new() { Id = "p1", Name = "DeepSeek", Provider = "openai", Model = "deepseek-chat", Models = new List<string> { "deepseek-chat" }, ApiKey = "k1" },
        };
        vm.RebuildModelChoices();

        Assert.Equal(2, vm.ModelChoices.Count);
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "deepseek-chat（DeepSeek）");
    }

    [Fact]
    public async Task SendAsync_SelectedProviderModel_CarriesProviderConfig()
    {
        ChatRequest? sentReq = null;
        var fake = CreateFake();
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            sentReq = req;
            var res = new ChatStreamResult { Model = "deepseek-chat", Provider = "openai" };
            onDone(res);
            return Task.FromResult(res);
        };

        var profile = new LlmProfile
        {
            Id = "p1",
            Name = "DeepSeek",
            Provider = "openai",
            BaseUrl = "https://api.deepseek.com/v1",
            Model = "deepseek-chat",
            Models = new List<string> { "deepseek-chat", "deepseek-reasoner" },
            ApiKey = "sk-request-key",
        };
        var settings = new AppSettings { LlmProfiles = new List<LlmProfile> { profile } };
        var vm = new ChatViewModel(fake, null, settings);

        // 点选某服务商的模型（非默认项）
        var choice = vm.ModelChoices.First(c => c.Model == "deepseek-chat" && c.Provider?.Id == "p1");
        vm.SelectedModelChoice = choice;
        vm.InputText = "你好";
        await vm.SendCommand.ExecuteAsync(null);

        // 请求携带该服务商配置（provider/key/url/模型），按请求生效
        Assert.NotNull(sentReq);
        Assert.NotNull(sentReq.ProviderConfig);
        Assert.Equal("openai", sentReq.ProviderConfig!.Provider);
        Assert.Equal("sk-request-key", sentReq.ProviderConfig.ApiKey);
        Assert.Equal("https://api.deepseek.com/v1", sentReq.ProviderConfig.BaseUrl);
        Assert.Equal("deepseek-chat", sentReq.ProviderConfig.Model);
        Assert.Equal("deepseek-chat", sentReq.Model);
    }

    [Fact]
    public async Task SendAsync_DefaultChoice_NoProviderConfig()
    {
        ChatRequest? sentReq = null;
        var fake = CreateFake();
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            sentReq = req;
            var res = new ChatStreamResult { Model = "m", Provider = "p" };
            onDone(res);
            return Task.FromResult(res);
        };

        var profile = new LlmProfile
        {
            Id = "p1",
            Name = "DeepSeek",
            Provider = "openai",
            Models = new List<string> { "deepseek-chat" },
            ApiKey = "k1",
        };
        var settings = new AppSettings { LlmProvider = "openai", LlmApiKey = "test-key", LlmProfiles = new List<LlmProfile> { profile } };
        var vm = new ChatViewModel(fake, null, settings);
        // 选中「设置页默认」伪项
        vm.SelectedModelChoice = vm.ModelChoices.First(c => c.IsDefault);
        vm.InputText = "你好";
        await vm.SendCommand.ExecuteAsync(null);

        // 默认项：不带 ProviderConfig / Model，用后端全局配置
        Assert.NotNull(sentReq);
        Assert.Null(sentReq.ProviderConfig);
        Assert.Null(sentReq.Model);
    }

    [Fact]
    public async Task SendAsync_CarriesPerUserGithubToken()
    {
        ChatRequest? sentReq = null;
        var fake = CreateFake();
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            sentReq = req;
            var res = new ChatStreamResult { Model = "m", Provider = "p" };
            onDone(res);
            return Task.FromResult(res);
        };

        // 本机用户自己的 GitHub Token（设置页配置，DPAPI 加密落盘）随请求携带，
        // 后端按请求生效，绝不作为全局配置共享给其他用户
        var settings = new AppSettings { LlmProvider = "openai", LlmApiKey = "test-key", GithubToken = "ghp_自己的令牌" };
        var vm = new ChatViewModel(fake, null, settings);
        vm.InputText = "帮我搜一下 GitHub 上的开源仓库";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.NotNull(sentReq);
        Assert.Equal("ghp_自己的令牌", sentReq!.GithubToken);
    }

    [Fact]
    public async Task SendAsync_NoGithubToken_SendsNull()
    {
        ChatRequest? sentReq = null;
        var fake = CreateFake();
        fake.OnChatStream = (req, onToken, onDone, _) =>
        {
            sentReq = req;
            var res = new ChatStreamResult { Model = "m", Provider = "p" };
            onDone(res);
            return Task.FromResult(res);
        };

        var vm = new ChatViewModel(fake, null, new AppSettings { LlmProvider = "openai", LlmApiKey = "test-key" });
        vm.InputText = "搜索 GitHub";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.NotNull(sentReq);
        Assert.Null(sentReq!.GithubToken);
    }

    [Fact]
    public void SelectBackToDefault_ResetsModelSelection()
    {
        var fake = CreateFake();
        var settings = new AppSettings
        {
            LlmApiKey = "test-key",
            LlmProfiles = new List<LlmProfile>
            {
                new() { Id = "p1", Name = "DeepSeek", Provider = "openai", Model = "deepseek-chat", Models = new List<string> { "deepseek-chat" }, ApiKey = "k1" },
            },
        };
        var vm = new ChatViewModel(fake, null, settings);

        // 先切到某服务商的模型
        vm.SelectedModelChoice = vm.ModelChoices.First(c => c.Model == "deepseek-chat");
        Assert.Equal("deepseek-chat", vm.SelectedModel);

        // 切回默认
        vm.SelectedModelChoice = vm.ModelChoices.First(c => c.IsDefault);
        Assert.Equal(ChatViewModel.DefaultModelLabel, vm.SelectedModel);
    }

    // ======================================================================
    // 撤回中间消息（增强）
    // ======================================================================

    [Fact]
    public async Task Withdraw_MiddleMessage_RemovesMessageAndFollowingResponses()
    {
        var fake = CreateFake();
        fake.OnChat = (_, _) => Task.FromResult(MakeResponse("回答"));
        var vm = CreateVm(fake);

        // 构造 3 轮对话
        vm.InputText = "第一轮";
        await vm.SendCommand.ExecuteAsync(null);
        vm.InputText = "第二轮";
        await vm.SendCommand.ExecuteAsync(null);
        vm.InputText = "第三轮";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.Equal(6, vm.Messages.Count); // 3 user + 3 assistant

        // 撤回第二轮（中间消息）
        var secondUserMsg = vm.Messages[2]; // idx 2 = 第二轮 user
        Assert.Equal("第二轮", secondUserMsg.Content);
        vm.WithdrawCommand.Execute(secondUserMsg);

        // 只剩第一轮（user+assistant），第二轮及之后全部移除
        Assert.Equal(2, vm.Messages.Count);
        Assert.Equal("第一轮", vm.Messages[0].Content);
        Assert.Equal("回答", vm.Messages[1].Content);
        // 第二轮内容回填到输入框
        Assert.Equal("第二轮", vm.InputText);
    }

    [Fact]
    public async Task Withdraw_MiddleMessage_KeepsPriorConversation()
    {
        var fake = CreateFake();
        fake.OnChat = (req, _) => Task.FromResult(MakeResponse($"回复: {req.Query}"));
        var vm = CreateVm(fake);

        // 构造 2 轮对话
        vm.InputText = "问题A";
        await vm.SendCommand.ExecuteAsync(null);
        vm.InputText = "问题B";
        await vm.SendCommand.ExecuteAsync(null);
        Assert.Equal(4, vm.Messages.Count);

        // 撤回第一轮
        vm.WithdrawCommand.Execute(vm.Messages[0]);

        // 第一轮被移除，只剩空列表（没有更早的消息）
        Assert.Empty(vm.Messages);
        Assert.Equal("问题A", vm.InputText);
    }

    [Fact]
    public async Task Withdraw_MiddleMessage_CanResendAndContinuesChatId()
    {
        var fake = CreateFake();
        var chatIds = new List<string?>();
        fake.OnChat = (req, _) =>
        {
            chatIds.Add(req.ChatId);
            return Task.FromResult(MakeResponse(chatId: "chat-123"));
        };
        var vm = CreateVm(fake);

        // 第一轮
        vm.InputText = "问题A";
        await vm.SendCommand.ExecuteAsync(null);
        // 第二轮
        vm.InputText = "问题B";
        await vm.SendCommand.ExecuteAsync(null);

        // 撤回第二轮
        vm.WithdrawCommand.Execute(vm.Messages[2]);
        Assert.Equal("问题B", vm.InputText);
        Assert.Equal(2, vm.Messages.Count); // 只剩第一轮

        // 修改后重新发送（续聊，保留 chatId）
        vm.InputText = "修改后的问题B";
        await vm.SendCommand.ExecuteAsync(null);

        Assert.Equal(4, vm.Messages.Count); // 第一轮 + 新的第二轮
        Assert.Equal("chat-123", chatIds[2]); // 第三轮请求带上 chatId
    }

    // --- FC-02 锁定测试：LLM 未配置时事前拦截 ---

    [Fact]
    public async Task SendAsync_WithoutLlmConfigured_InterceptsWithGuidanceAndNoRequest()
    {
        // Given: LLM 未配置
        var fake = CreateFake();
        var vm = new ChatViewModel(fake, null, new AppSettings());
        var chatCalled = false;

        fake.OnChat = (_, _) =>
        {
            chatCalled = true;
            return Task.FromResult(MakeResponse());
        };

        // When: 发送消息
        vm.InputText = "问题";
        await vm.SendCommand.ExecuteAsync(null);

        // Then: 事前拦截，不发网络请求，不产生消息
        Assert.False(vm.IsLlmConfigured);
        Assert.False(chatCalled, "OnChat should not be called when LLM is not configured");
        Assert.Empty(vm.Messages);
        Assert.Contains("尚未配置大模型", vm.StatusMessage);
    }
}