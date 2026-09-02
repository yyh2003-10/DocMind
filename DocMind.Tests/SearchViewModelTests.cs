using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using CommunityToolkit.Mvvm.Input;

namespace DocMind.Tests;

public class SearchViewModelTests
{
    private static SearchViewModel CreateVm(FakeDoc2kbApiService fake)
        => new(fake);

    [Fact]
    public async Task LoadCollectionsAsync_PopulatesAvailableCollections()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnGetStats = (_, _) => Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]>
                {
                    ["colA"] = new[] { 1, 1 },
                    ["colB"] = new[] { 2, 2 },
                }
            })
        };

        var vm = CreateVm(fake);
        await vm.LoadCollectionsAsync();

        Assert.Contains(SearchViewModel.AllCollectionsLabel, vm.AvailableCollections);
        Assert.Contains("colA", vm.AvailableCollections);
        Assert.Contains("colB", vm.AvailableCollections);
    }

    [Fact]
    public async Task SearchAsync_WithHits_SelectsFirstHitAndEnablesActions()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnSearch = (_, _) => Task.FromResult(new SearchResponse
            {
                Total = 1,
                ElapsedMs = 12,
                Hits = new List<SearchHit>
                {
                    new SearchHit
                    {
                        Source = "doc1.pdf",
                        Content = "这是一段测试分块内容",
                        Score = 0.85,
                        Format = "pdf"
                    }
                }
            })
        };

        var vm = CreateVm(fake);
        vm.Query = "测试";

        string? openedDoc = null;
        vm.OpenDocumentRequested += src => openedDoc = src;

        string? chatPrompt = null;
        vm.AskInChatRequested += p => chatPrompt = p;

        await vm.SearchCommand.ExecuteAsync(null);

        Assert.True(vm.HasHits);
        Assert.False(vm.ShowEmptyGuide);
        Assert.NotNull(vm.SelectedHit);
        Assert.Equal("doc1.pdf", vm.SelectedHit.Source);

        Assert.True(vm.OpenInDocumentsCommand.CanExecute(null));
        Assert.True(vm.AskInChatCommand.CanExecute(null));

        vm.OpenInDocumentsCommand.Execute(null);
        Assert.Equal("doc1.pdf", openedDoc);

        vm.AskInChatCommand.Execute(null);
        Assert.NotNull(chatPrompt);
        Assert.Contains("doc1.pdf", chatPrompt);
    }

    [Fact]
    public async Task SearchAsync_UsesBackendEmptyResultMessage()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnSearch = (_, _) => Task.FromResult(new SearchResponse
            {
                Total = 0,
                Message = "知识库为空：请先导入文档",
            }),
        };

        var vm = CreateVm(fake);
        vm.Query = "不存在的内容";

        await vm.SearchCommand.ExecuteAsync(null);

        Assert.Equal("知识库为空：请先导入文档", vm.StatusMessage);
        Assert.True(vm.ShowEmptyGuide);
    }

    // ======================================================================
    // 搜索历史（增强）
    // ======================================================================

    [Fact]
    public async Task SearchAsync_AddsQueryToHistory()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnSearch = (_, _) => Task.FromResult(new SearchResponse { Total = 0 })
        };

        var vm = CreateVm(fake);
        vm.Query = "深度学习";
        await vm.SearchCommand.ExecuteAsync(null);

        Assert.Single(vm.SearchHistory);
        Assert.Equal("深度学习", vm.SearchHistory[0]);
        Assert.True(vm.HasSearchHistory);
    }

    [Fact]
    public async Task SearchAsync_DeduplicatesHistory()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnSearch = (_, _) => Task.FromResult(new SearchResponse { Total = 0 })
        };

        var vm = CreateVm(fake);
        vm.Query = "机器学习";
        await vm.SearchCommand.ExecuteAsync(null);
        vm.Query = "机器学习";
        await vm.SearchCommand.ExecuteAsync(null);

        Assert.Single(vm.SearchHistory); // 去重，只保留一条
        Assert.Equal("机器学习", vm.SearchHistory[0]);
    }

    [Fact]
    public async Task SearchAsync_MovesDuplicateToFront()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnSearch = (_, _) => Task.FromResult(new SearchResponse { Total = 0 })
        };

        var vm = CreateVm(fake);
        vm.Query = "第一个查询";
        await vm.SearchCommand.ExecuteAsync(null);
        vm.Query = "第二个查询";
        await vm.SearchCommand.ExecuteAsync(null);
        vm.Query = "第一个查询";
        await vm.SearchCommand.ExecuteAsync(null);

        Assert.Equal(2, vm.SearchHistory.Count);
        Assert.Equal("第一个查询", vm.SearchHistory[0]); // 最新的在前
        Assert.Equal("第二个查询", vm.SearchHistory[1]);
    }

    [Fact]
    public void RemoveHistoryItem_RemovesFromList()
    {
        var fake = new FakeDoc2kbApiService();
        var vm = CreateVm(fake);
        vm.SearchHistory.Add("查询A");
        vm.SearchHistory.Add("查询B");

        vm.RemoveHistoryItemCommand.Execute("查询A");

        Assert.Single(vm.SearchHistory);
        Assert.Equal("查询B", vm.SearchHistory[0]);
    }

    [Fact]
    public void ClearHistory_EmptiesList()
    {
        var fake = new FakeDoc2kbApiService();
        var vm = CreateVm(fake);
        vm.SearchHistory.Add("查询A");
        vm.SearchHistory.Add("查询B");

        vm.ClearHistoryCommand.Execute(null);

        Assert.Empty(vm.SearchHistory);
        Assert.False(vm.HasSearchHistory);
    }

    [Fact]
    public void SelectHistoryItem_FillsQueryAndSearches()
    {
        var searched = false;
        var fake = new FakeDoc2kbApiService
        {
            OnSearch = (_, _) =>
            {
                searched = true;
                return Task.FromResult(new SearchResponse { Total = 0 });
            }
        };

        var vm = CreateVm(fake);
        vm.SearchHistory.Add("历史查询");

        vm.SelectHistoryItemCommand.Execute("历史查询");

        Assert.Equal("历史查询", vm.Query);
        Assert.False(vm.ShowHistory);
        Assert.True(searched);
    }

    // ======================================================================
    // 高亮辅助（增强）
    // ======================================================================

    [Fact]
    public void HighlightTerms_WrapsMatchingTerms()
    {
        var result = SearchViewModel.HighlightTerms("深度学习是人工智能的核心技术", "学习");
        Assert.Contains("\u231c", result); // ⌜
        Assert.Contains("\u231d", result); // ⌝
        Assert.Contains("深度\u231c学习\u231d是人工智能的核心技术", result);
    }

    [Fact]
    public void HighlightTerms_CaseInsensitive()
    {
        var result = SearchViewModel.HighlightTerms("Hello World hello", "hello");
        // 两个 hello 都应被高亮
        Assert.Equal(2, result.Count(c => c == '\u231c'));
    }

    [Fact]
    public void HighlightTerms_SkipsShortTerms()
    {
        var result = SearchViewModel.HighlightTerms("abc def ghi", "a b");
        // 单字符词不高亮
        Assert.DoesNotContain("\u231c", result);
        Assert.Equal("abc def ghi", result);
    }

    [Fact]
    public void HighlightTerms_NullQuery_ReturnsOriginal()
    {
        Assert.Equal("原文", SearchViewModel.HighlightTerms("原文", null));
        Assert.Equal("原文", SearchViewModel.HighlightTerms("原文", ""));
    }

    [Fact]
    public async Task SearchAsync_PersistsHistoryToAppSettings()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnSearch = (_, _) => Task.FromResult(new SearchResponse { Total = 0 })
        };
        var settings = new AppSettings();
        var vm = new SearchViewModel(fake, settings);

        vm.Query = "持久化测试";
        await vm.SearchCommand.ExecuteAsync(null);

        Assert.Contains("持久化测试", settings.SearchHistory);
    }
}
