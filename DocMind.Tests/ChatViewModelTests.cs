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

        var settings = new AppSettings
        {
            LlmProfiles = new List<LlmProfile>
            {
                new() { Id = "p1", Name = "Ollama 本地", Provider = "ollama", Model = "qwen2.5:7b", Models = new List<string> { "qwen2.5:7b" } },
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

    [Fact]
    public void MarkdownHeading_DownscaledToBodySize_NotReportLike()
    {
        // 气泡里 ###/#### 标题不应渲染成 18px+ 大标题（文档报告感），
        // 应降级为与正文一致的 15px 加粗，保持聊天语气
        RunOnSta(() =>
        {
            var msg = new ChatMessage { Role = "assistant" };
            msg.Content = "### 标题\n\n#### 副标题\n\n正文内容";

            Assert.NotNull(msg.RenderedDocument);
            var paras = FindParagraphs(msg.RenderedDocument!).ToList();
            var heading = paras.FirstOrDefault(p => p.Inlines.OfType<Run>().Any(r => r.Text.Contains("标题")));
            Assert.NotNull(heading);
            Assert.True(heading!.FontSize <= 15, $"标题字号应为 15 以内，实际 {heading.FontSize}");
        });
    }

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
        // 推理链增量累积为 ThinkingText
        Assert.Equal("用户想了解 B3 规格需要核对官方手册", msg.ThinkingText);
        Assert.True(msg.HasThinkingText);
        // token 统计与搜索摘要
        Assert.Equal(2, msg.TokenCount);
        Assert.Equal("搜索到 2 个网页 · 浏览 1 个页面", msg.SearchSummaryText);
        Assert.Contains("2 tok", msg.TokenStatText);
        Assert.Contains("3 个来源", msg.TokenStatText);
        Assert.Contains("5000ms", msg.TokenStatText);
    }

    [Fact]
    public void WebSearchCheckbox_LoadsFromSettings_AndPersistsOnToggle()
    {
        var settings = new AppSettings { EnableWebSearch = true };
        var vm = new ChatViewModel(CreateFake(), null, settings);

        // 构造时恢复上次勾选状态
        Assert.True(vm.IsWebSearchEnabled);

        // 取消勾选 → 写回 AppSettings 并落盘（下次启动保持未勾选）
        vm.IsWebSearchEnabled = false;
        Assert.False(settings.EnableWebSearch);

        // 重新勾选 → 写回并落盘
        vm.IsWebSearchEnabled = true;
        Assert.True(settings.EnableWebSearch);
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
        // 开始生成回答时保持思考区展开：用户想看 AI 真实的思考全链路（不自动收起）
        Assert.True(msg.IsThinkingExpanded);
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
    public void Constructor_LoadsModelChoices_DefaultFirst_SelectsActiveDefaultModel()
    {
        var fake = CreateFake();
        var active = new LlmProfile
        {
            Id = "p1",
            Name = "DeepSeek",
            Provider = "openai",
            Model = "deepseek-chat",
            Models = new List<string> { "deepseek-chat", "deepseek-reasoner" },
        };
        var settings = new AppSettings
        {
            LlmProfiles = new List<LlmProfile>
            {
                active,
                new LlmProfile { Id = "p2", Name = "Ollama 本地", Provider = "ollama", Model = "llama3.2", Models = new List<string> { "llama3.2" } },
            },
            ActiveProfileId = "p1",
        };

        var vm = new ChatViewModel(fake, null, settings);

        // 首项为「设置页默认」伪值，其后为各启用服务商的模型（「模型名（服务商名）」）
        Assert.Equal(1 + 2 + 1, vm.ModelChoices.Count);
        Assert.True(vm.ModelChoices[0].IsDefault);
        Assert.Null(vm.ModelChoices[0].Provider);
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "deepseek-chat（DeepSeek）");
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "deepseek-reasoner（DeepSeek）");
        Assert.Contains(vm.ModelChoices, c => c.DisplayName == "llama3.2（Ollama 本地）");
        // 默认选中最后应用的档案的默认模型（仅高亮，不触发请求）
        Assert.Equal("deepseek-chat", vm.SelectedModelChoice?.Model);
        Assert.Equal("p1", vm.SelectedModelChoice?.Provider?.Id);
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
        var settings = new AppSettings { LlmProfiles = new List<LlmProfile> { disabled } };

        var vm = new ChatViewModel(fake, null, settings);

        // 停用的服务商不出现在点选列表（配置保留）
        Assert.Single(vm.ModelChoices);
        Assert.True(vm.ModelChoices[0].IsDefault);
    }

    [Fact]
    public void RebuildModelChoices_ReflectsLatestProviders()
    {
        var fake = CreateFake();
        var settings = new AppSettings();
        var vm = new ChatViewModel(fake, null, settings);
        Assert.Single(vm.ModelChoices); // 仅默认项

        // 模拟设置页新增服务商（事件驱动 RebuildModelChoices）
        settings.LlmProfiles = new List<LlmProfile>
        {
            new() { Id = "p1", Name = "DeepSeek", Provider = "openai", Model = "deepseek-chat", Models = new List<string> { "deepseek-chat" } },
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
        };
        var settings = new AppSettings { LlmProfiles = new List<LlmProfile> { profile } };
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
        var settings = new AppSettings { GithubToken = "ghp_自己的令牌" };
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

        var vm = new ChatViewModel(fake, null, new AppSettings());
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
            LlmProfiles = new List<LlmProfile>
            {
                new() { Id = "p1", Name = "DeepSeek", Provider = "openai", Model = "deepseek-chat", Models = new List<string> { "deepseek-chat" } },
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
}