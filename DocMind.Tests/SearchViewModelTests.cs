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
                Collections = new Dictionary<string, (int, int, long)>
                {
                    ["colA"] = (1, 1, 100),
                    ["colB"] = (2, 2, 200),
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
                ElapsedMs = 12.5,
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
}
