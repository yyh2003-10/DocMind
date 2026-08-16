using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Tests;

/// <summary>
/// DocumentsViewModel 导航自动加载：EnsureLoadedAsync 幂等性。
/// </summary>
public class DocumentsViewModelTests
{
    private static DocumentsViewModel CreateVm(FakeDoc2kbApiService fake)
        => new(fake, new NotificationService());

    [Fact]
    public async Task EnsureLoadedAsync_LoadsOnceAndIsIdempotent()
    {
        var loadCount = 0;
        var fake = new FakeDoc2kbApiService();
        fake.OnListDocuments = (_, _, _, _, _, _) =>
        {
            loadCount++;
            return Task.FromResult(new DocumentListResponse
            {
                Documents = new[]
                {
                    new Document { Id = "d1", Source = "doc1.md", Format = "md", ChunkCount = 5 },
                },
                Total = 1,
                Page = 1,
                PageSize = 20,
            });
        };

        var vm = CreateVm(fake);

        // 首次加载
        await vm.EnsureLoadedAsync();
        Assert.Equal(1, loadCount);
        Assert.Single(vm.Documents);

        // 第二次调用幂等（不重复加载）
        await vm.EnsureLoadedAsync();
        Assert.Equal(1, loadCount); // 未增加
    }
}