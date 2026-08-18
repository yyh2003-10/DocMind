using DocMind.Models;
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
}
