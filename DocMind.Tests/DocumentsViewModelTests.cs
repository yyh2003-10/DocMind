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
        fake.OnListDocuments = (_, _, _, _, _, _, _) =>
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

    [Fact]
    public async Task SearchQuery_ResetsPage_AndTriggersRefresh()
    {
        var loadCount = 0;
        string? capturedQ = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnListDocuments = (_, _, _, _, _, q, _) =>
        {
            loadCount++;
            capturedQ = q;
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
        await vm.EnsureLoadedAsync();
        Assert.Equal(1, loadCount);

        // 设置搜索关键词 → 触发防抖刷新
        vm.SearchQuery = "doc1";
        // 等防抖 300ms + 刷新完成
        await Task.Delay(500);
        Assert.Equal(2, loadCount);
        Assert.Equal("doc1", capturedQ);
        Assert.Equal(1, vm.Page); // 重置为第 1 页
    }

    [Fact]
    public async Task FilterFormat_PassesToApi()
    {
        string? capturedFormat = null;
        var fake = new FakeDoc2kbApiService();
        fake.OnListDocuments = (_, _, _, fmt, _, _, _) =>
        {
            capturedFormat = fmt;
            return Task.FromResult(new DocumentListResponse
            {
                Documents = Array.Empty<Document>(),
                Total = 0,
                Page = 1,
                PageSize = 20,
            });
        };

        var vm = CreateVm(fake);
        await vm.EnsureLoadedAsync();

        vm.FilterFormat = "pdf";
        // 设置 FilterFormat 直接触发 RefreshAsync(无防抖)
        await Task.Delay(200);
        Assert.Equal("pdf", capturedFormat);
    }
}