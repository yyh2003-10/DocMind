using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Tests;

public class GraphViewModelTests
{
    [Fact]
    public async Task EnsureLoadedAsync_LoadsGraphAndPopulatesCollections()
    {
        var loadCount = 0;
        var fake = new FakeDoc2kbApiService();

        fake.OnGetStats = (_, _) => Task.FromResult(new Stats
        {
            TotalDocuments = 5,
            TotalChunks = 20,
            Collections = new Dictionary<string, int[]> { { "kb1", [2, 10, 1000] }, { "kb2", [3, 10, 1500] } }
        });

        fake.OnGetGraph = (coll, limit, _) =>
        {
            loadCount++;
            var nodes = new List<GraphNode>
            {
                new("n1", "Node1", "tech", "tech", 2, "kb1"),
                new("n2", "Node2", "concept", "concept", 1, "kb1")
            };
            var edges = new List<GraphEdge>
            {
                new("n1", "n2", "relates")
            };
            return Task.FromResult(new GraphResponse(nodes, edges, 2));
        };

        var vm = new GraphViewModel(fake);

        // 首次加载
        await vm.EnsureLoadedAsync();
        Assert.Equal(1, loadCount);
        Assert.True(vm.HasGraph);
        Assert.Equal(2, vm.TotalNodes);
        Assert.Equal(1, vm.TotalEdges);
        Assert.Contains("全部集合", vm.Collections);
        Assert.Contains("kb1", vm.Collections);
        Assert.Contains("kb2", vm.Collections);

        // 再次加载幂等
        await vm.EnsureLoadedAsync();
        Assert.Equal(1, loadCount);
    }

    [Fact]
    public async Task SelectNodeAsync_PopulatesSelectedNodeAndRelations()
    {
        var fake = new FakeDoc2kbApiService();
        var nodes = new List<GraphNode>
        {
            new("n1", "Node1", "tech", "tech", 2, "default"),
            new("n2", "Node2", "concept", "concept", 1, "default")
        };
        fake.OnGetGraph = (_, _, _) => Task.FromResult(new GraphResponse(nodes, new List<GraphEdge>(), 2));
        fake.OnGetEntityDetail = (eid, _, _) => Task.FromResult(new GraphEntityDetailResponse(
            new GraphNode("n1", "Node1", "tech", "tech", 2, "default"),
            new List<GraphEntityRelation> { new(1, "n1", "Node1", "tech", "n2", "Node2", "concept", "uses") },
            new List<GraphContextSnippet> { new(101, "doc1", "public class Node1 { ... }", "E:/code/Node1.cs", "Class Definition", 1, "Node1.cs", "Doc Summary") },
            new List<GraphSourceDocument> { new("E:/code/Node1.cs", "Node1.cs", "Doc Summary", 1) }
        ));

        var vm = new GraphViewModel(fake);
        await vm.LoadGraphAsync();

        await vm.SelectNodeAsync("n1");

        Assert.True(vm.IsDetailOpen);
        Assert.NotNull(vm.SelectedNode);
        Assert.Equal("Node1", vm.SelectedNode.Name);
        Assert.Single(vm.SelectedNodeRelations);
        Assert.Equal("uses", vm.SelectedNodeRelations[0].Relation);
        Assert.Single(vm.ContextSnippets);
        Assert.Equal("public class Node1 { ... }", vm.ContextSnippets[0].Content);
        Assert.Single(vm.SourceDocuments);
        Assert.Equal("Node1.cs", vm.SourceDocuments[0].DisplayTitle);
        Assert.True(vm.HasSnippets);
        Assert.True(vm.HasSourceDocuments);
        Assert.True(vm.HasRelations);

        vm.CloseDetail();
        Assert.False(vm.IsDetailOpen);
        Assert.Null(vm.SelectedNode);
        Assert.Empty(vm.SelectedNodeRelations);
        Assert.Empty(vm.ContextSnippets);
        Assert.Empty(vm.SourceDocuments);
        Assert.False(vm.HasSnippets);
    }

    [Fact]
    public async Task ExtractGraphAsync_CallsApiAndReloadsGraph()
    {
        var fake = new FakeDoc2kbApiService();
        var extracted = false;
        var loaded = false;

        fake.OnExtractGraph = (coll, topK, _) =>
        {
            extracted = true;
            return Task.FromResult(new GraphExtractResult(true, 3, 0, new List<string>(), 80));
        };

        fake.OnGetGraph = (coll, limit, _) =>
        {
            loaded = true;
            var nodes = new List<GraphNode> { new("n1", "Node1", "tech", "tech", 1, "default") };
            return Task.FromResult(new GraphResponse(nodes, new List<GraphEdge>(), 1));
        };

        var vm = new GraphViewModel(fake);
        await vm.ExtractGraphCommand.ExecuteAsync(null);

        Assert.True(extracted);
        Assert.True(loaded);
        Assert.Equal(1, vm.TotalNodes);
        Assert.True(vm.HasGraph);
    }

    [Fact]
    public async Task NavigateToEntityAsync_SelectsTargetNodeAndFiresFocusEvent()
    {
        var fake = new FakeDoc2kbApiService();
        var nodes = new List<GraphNode>
        {
            new("n1", "Node1", "tech", "tech", 2, "default"),
            new("n2", "Node2", "concept", "concept", 1, "default")
        };
        fake.OnGetGraph = (_, _, _) => Task.FromResult(new GraphResponse(nodes, new List<GraphEdge>(), 2));
        fake.OnGetEntityRelations = (eid, _, _) => Task.FromResult(new List<GraphEntityRelation>
        {
            new(1, "n1", "Node1", "tech", "n2", "Node2", "concept", "uses")
        });

        var vm = new GraphViewModel(fake);
        await vm.LoadGraphAsync();

        string? focusedNodeId = null;
        vm.NodeFocusRequested += id => focusedNodeId = id;

        // 选中 n1
        await vm.SelectNodeAsync("n1");
        Assert.Equal("n1", vm.SelectedNode?.Id);

        // 从 n1 导航到关联关系中的 n2
        var rel = vm.SelectedNodeRelations[0];
        await vm.NavigateToEntityCommand.ExecuteAsync(rel);

        Assert.Equal("n2", vm.SelectedNode?.Id);
        Assert.Equal("n2", focusedNodeId);
    }

    [Fact]
    public async Task SelectNodeAsync_PopulatesAdaptiveQuickPrompts()
    {
        var fake = new FakeDoc2kbApiService();
        var nodes = new List<GraphNode>
        {
            new("n1", "MyService", "tech", "tech", 2, "default"),
            new("n2", "DDD Pattern", "concept", "concept", 1, "default")
        };
        fake.OnGetGraph = (_, _, _) => Task.FromResult(new GraphResponse(nodes, new List<GraphEdge>(), 2));

        var vm = new GraphViewModel(fake);
        await vm.LoadGraphAsync();

        // 选中代码/技术类实体
        await vm.SelectNodeAsync("n1");
        Assert.NotEmpty(vm.AdaptiveQuickPrompts);
        Assert.Contains(vm.AdaptiveQuickPrompts, p => p.Contains("核心机制"));

        // 选中概念/架构类实体
        await vm.SelectNodeAsync("n2");
        Assert.NotEmpty(vm.AdaptiveQuickPrompts);
        Assert.Contains(vm.AdaptiveQuickPrompts, p => p.Contains("通俗解释") || p.Contains("优缺点"));
    }

    [Fact]
    public async Task DistillAndIngestEntityKnowledge_CompletesFullLifecycle()
    {
        var fake = new FakeDoc2kbApiService();
        var nodes = new List<GraphNode>
        {
            new("n1", "WebSearchService", "tech", "tech", 2, "default")
        };
        fake.OnGetGraph = (_, _, _) => Task.FromResult(new GraphResponse(nodes, new List<GraphEdge>(), 1));
        fake.OnGetEntityDetail = (eid, _, _) => Task.FromResult(new GraphEntityDetailResponse(
            nodes[0],
            new List<GraphEntityRelation>(),
            new List<GraphContextSnippet> { new(1, "doc", "class WebSearchService { ... }", "web_search.py", "Heading", 1, "Title", "Summary") }
        ));

        var distillCalled = false;
        fake.OnDistillEntityKnowledge = (req, _) =>
        {
            distillCalled = true;
            Assert.Equal("n1", req.EntityId);
            Assert.Equal("WebSearchService", req.EntityName);
            return Task.FromResult(new EntityDistillResponse
            {
                EntityId = req.EntityId,
                EntityName = req.EntityName,
                MarkdownCard = "# 📚【知识档案】WebSearchService\n## 📌 核心定义\n实时联网检索",
                SuggestedTags = new List<string> { "tech", "WebSearchService", "search" },
                Model = "test-model"
            });
        };

        var ingestCalled = false;
        fake.OnIngestText = (req, _) =>
        {
            ingestCalled = true;
            Assert.Contains("WebSearchService", req.Title ?? "");
            Assert.Contains("实时联网检索", req.Text);
            return Task.FromResult(new IngestResponse { TotalDocuments = 1, TotalChunks = 100 });
        };

        var vm = new GraphViewModel(fake);
        await vm.LoadGraphAsync();
        await vm.SelectNodeAsync("n1");

        // 触发知识蒸馏
        await vm.DistillEntityCardCommand.ExecuteAsync(null);
        Assert.True(distillCalled);
        Assert.True(vm.IsDistillDialogOpen);
        Assert.Contains("实时联网检索", vm.DistilledMarkdownCard);
        Assert.Equal(3, vm.DistilledTags.Count);

        // 触发沉淀入库
        await vm.IngestDistilledCardCommand.ExecuteAsync(null);
        Assert.True(ingestCalled);
        Assert.False(vm.IsDistillDialogOpen);
    }

    [Fact]
    public async Task EnsureLoadedAsync_ShowsErrorAndRetriesAfterGraphFailure()
    {
        var attempts = 0;
        var fake = new FakeDoc2kbApiService
        {
            OnGetStats = (_, _) => Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]> { { "default", [0, 0, 0] } },
            }),
            OnGetGraph = (_, _, _) =>
            {
                attempts++;
                if (attempts == 1)
                {
                    throw new BackendConnectionException("图谱服务暂时不可达");
                }
                return Task.FromResult(new GraphResponse(
                    new List<GraphNode> { new("n1", "Node1", "concept", "concept", 1, "default") },
                    new List<GraphEdge>(),
                    1));
            },
        };

        var vm = new GraphViewModel(fake);
        await vm.EnsureLoadedAsync();

        Assert.True(vm.HasLoadError);
        Assert.True(vm.ShowGraphError);
        Assert.False(vm.ShowEmptyGraph);

        await vm.EnsureLoadedAsync();

        Assert.Equal(2, attempts);
        Assert.False(vm.HasLoadError);
        Assert.True(vm.HasGraph);
    }

    [Fact]
    public async Task DistillEntityCard_WithAppSettings_PassesModelAndProviderConfig()
    {
        var fake = new FakeDoc2kbApiService();
        var nodes = new List<GraphNode>
        {
            new("n1", "WebSearchService", "tech", "tech", 2, "default")
        };
        fake.OnGetGraph = (_, _, _) => Task.FromResult(new GraphResponse(nodes, new List<GraphEdge>(), 1));
        fake.OnGetEntityDetail = (eid, _, _) => Task.FromResult(new GraphEntityDetailResponse(
            new GraphNode("n1", "WebSearchService", "tech", "tech", 2, "default"),
            new List<GraphEntityRelation>(),
            new List<GraphContextSnippet>(),
            new List<GraphSourceDocument>()
        ));

        EntityDistillRequest? capturedReq = null;
        fake.OnDistillEntityKnowledge = (req, _) =>
        {
            capturedReq = req;
            return Task.FromResult(new EntityDistillResponse
            {
                EntityId = req.EntityId,
                EntityName = req.EntityName,
                MarkdownCard = "# 知识卡片",
                SuggestedTags = new List<string> { "tech" },
                Model = req.Model ?? "default"
            });
        };

        var settings = new AppSettings
        {
            LlmProvider = "openai",
            LlmApiKey = "sk-test-key",
            LlmBaseUrl = "https://api.openai.com/v1",
            LlmModel = "gpt-4o",
            ActiveProfileId = "p1",
            LlmProfiles = new List<LlmProfile>
            {
                new()
                {
                    Id = "p1",
                    Name = "DeepSeek Profile",
                    Provider = "openai",
                    ApiKey = "sk-deepseek-key",
                    BaseUrl = "https://api.deepseek.com",
                    Model = "deepseek-chat",
                    Temperature = 0.3,
                    MaxTokens = 4096
                }
            }
        };

        var vm = new GraphViewModel(fake, null, settings);
        await vm.LoadGraphAsync();
        await vm.SelectNodeAsync("n1");

        await vm.DistillEntityCardCommand.ExecuteAsync(null);

        Assert.NotNull(capturedReq);
        Assert.Equal("deepseek-chat", capturedReq.Model);
        Assert.NotNull(capturedReq.ProviderConfig);
        Assert.Equal("openai", capturedReq.ProviderConfig.Provider);
        Assert.Equal("sk-deepseek-key", capturedReq.ProviderConfig.ApiKey);
        Assert.Equal("https://api.deepseek.com", capturedReq.ProviderConfig.BaseUrl);
        Assert.Equal("deepseek-chat", capturedReq.ProviderConfig.Model);
    }

    [Fact]
    public async Task LoadGraphAsync_StatsAuthoritative_OverridesVisualizeCounts()
    {
        // 权威计数来自 /v1/graph/stats（AUD-017 接通），即使 visualize 被 limit 截断也应显示全集数字
        var fake = new FakeDoc2kbApiService();
        fake.OnGetGraph = (coll, limit, _) => Task.FromResult(new GraphResponse(
            new List<GraphNode> { new("n1", "Node1", "tech", "tech", 1, "default") },
            new List<GraphEdge>(),
            1));
        fake.OnGetGraphStats = (coll, _) => Task.FromResult(new GraphStats
        {
            EntityCount = 120,
            RelationCount = 340,
            Collection = null
        });

        var vm = new GraphViewModel(fake);
        await vm.LoadGraphAsync();

        Assert.Equal(120, vm.TotalNodes);
        Assert.Equal(340, vm.TotalEdges);
        Assert.True(vm.HasGraph);
    }

    [Fact]
    public async Task LoadGraphAsync_StatsUnavailable_FallsBackToVisualizeCounts()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetGraph = (coll, limit, _) => Task.FromResult(new GraphResponse(
            new List<GraphNode>
            {
                new("n1", "Node1", "tech", "tech", 2, "default"),
                new("n2", "Node2", "concept", "concept", 1, "default"),
            },
            new List<GraphEdge> { new("n1", "n2", "relates") },
            2));
        // 默认 Fake 的 OnGetGraphStats 返回 null → 应优雅回退可视化计数

        var vm = new GraphViewModel(fake);
        await vm.LoadGraphAsync();

        Assert.Equal(2, vm.TotalNodes);
        Assert.Equal(1, vm.TotalEdges);
    }

    [Fact]
    public async Task LoadGraphAsync_StatsThrows_FallsBackToVisualizeCounts()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetGraph = (coll, limit, _) => Task.FromResult(new GraphResponse(
            new List<GraphNode> { new("n1", "Node1", "tech", "tech", 1, "default") },
            new List<GraphEdge>(),
            1));
        fake.OnGetGraphStats = (coll, _) => throw new BackendConnectionException("stats 不可达");

        var vm = new GraphViewModel(fake);
        await vm.LoadGraphAsync();

        Assert.Equal(1, vm.TotalNodes);
        Assert.Equal(0, vm.TotalEdges);
        Assert.True(vm.HasGraph);
    }

    [Fact]
    public async Task LoadGraphAsync_PopulatesEntityQuickJumpNames()
    {
        var fake = new FakeDoc2kbApiService();
        fake.OnGetGraph = (coll, limit, _) => Task.FromResult(new GraphResponse(
            new List<GraphNode> { new("n1", "Node1", "tech", "tech", 1, "default") },
            new List<GraphEdge>(),
            1));
        fake.OnGetGraphEntities = (coll, limit, _) => Task.FromResult(new List<GraphNode>
        {
            new("n1", "Alpha", "tech", "tech", 1, "default"),
            new("n2", "Beta", "concept", "concept", 1, "default"),
            new("n3", "Beta", "tech", "tech", 1, "default"),  // 同名去重
        });

        var vm = new GraphViewModel(fake);
        await vm.LoadGraphAsync();

        Assert.Contains("Alpha", vm.EntityQuickJumpNames);
        Assert.Contains("Beta", vm.EntityQuickJumpNames);
        Assert.Equal(1, vm.EntityQuickJumpNames.Count(n => n == "Beta"));
    }

    [Fact]
    public async Task QuickJump_Selection_FocusesNodeAndOpensDetail()
    {
        var fake = new FakeDoc2kbApiService();
        var nodes = new List<GraphNode>
        {
            new("n1", "Node1", "tech", "tech", 2, "default"),
            new("n2", "Node2", "concept", "concept", 1, "default"),
        };
        fake.OnGetGraph = (_, _, _) => Task.FromResult(new GraphResponse(nodes, new List<GraphEdge>(), 2));
        fake.OnGetEntityDetail = (eid, _, _) => Task.FromResult(new GraphEntityDetailResponse(
            nodes.First(n => n.Id == eid),
            new List<GraphEntityRelation>(),
            new List<GraphContextSnippet>(),
            new List<GraphSourceDocument>()
        ));

        var vm = new GraphViewModel(fake);
        await vm.LoadGraphAsync();

        string? focusedNodeId = null;
        vm.NodeFocusRequested += id => focusedNodeId = id;

        vm.SelectedEntityJumpName = "Node2";

        Assert.Equal("n2", focusedNodeId);
        Assert.True(vm.IsDetailOpen);
        Assert.Equal("n2", vm.SelectedNode?.Id);
    }

    [Fact]
    public void IsLlmConfigured_UnconfiguredAppSettings_ReturnsFalse()
    {
        var fake = new FakeDoc2kbApiService();
        var settings = new AppSettings(); // 默认 LlmProvider = "none"
        var vm = new GraphViewModel(fake, null, settings);

        Assert.False(vm.IsLlmConfigured);
    }

    [Fact]
    public void IsLlmConfigured_ConfiguredProvider_ReturnsTrue()
    {
        var fake = new FakeDoc2kbApiService();
        var settings = new AppSettings { LlmProvider = "openai", LlmApiKey = "test-key" };
        var vm = new GraphViewModel(fake, null, settings);

        Assert.True(vm.IsLlmConfigured);
    }

    [Fact]
    public void IsLlmConfigured_EnabledOllamaProfileWithoutKey_ReturnsTrue()
    {
        var settings = new AppSettings
        {
            LlmProvider = "none",
            LlmProfiles = new List<LlmProfile>
            {
                new() { Id = "ollama", Name = "本地 Ollama", Provider = "ollama", IsEnabled = true }
            }
        };
        var vm = new GraphViewModel(new FakeDoc2kbApiService(), null, settings);

        Assert.True(vm.IsLlmConfigured);
    }

    [Fact]
    public async Task ExtractGraphAsync_Unconfigured_DoesNotCallApi()
    {
        var fake = new FakeDoc2kbApiService();
        var extracted = false;
        fake.OnExtractGraph = (_, _, _) =>
        {
            extracted = true;
            return Task.FromResult(new GraphExtractResult(true, 0, 0, new List<string>(), 0));
        };
        var settings = new AppSettings(); // 未配置 LLM
        var vm = new GraphViewModel(fake, null, settings);

        await vm.ExtractGraphCommand.ExecuteAsync(null);

        Assert.False(extracted);
        Assert.Contains("尚未配置大模型", vm.StatusMessage);
    }

    [Fact]
    public void NavigateToSettingsRequested_FiresOnCommand()
    {
        var fake = new FakeDoc2kbApiService();
        var settings = new AppSettings();
        var vm = new GraphViewModel(fake, null, settings);

        var fired = false;
        vm.NavigateToSettingsRequested += () => fired = true;

        vm.NavigateToSettingsCommand.Execute(null);

        Assert.True(fired);
    }
}
