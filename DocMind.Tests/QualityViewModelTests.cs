using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Tests;

/// <summary>
/// QualityViewModel 导航自动加载：EnsureLoadedAsync 幂等性。
/// </summary>
public class QualityViewModelTests
{
    private static QualityViewModel CreateVm(FakeDoc2kbApiService fake)
        => new(fake);

    [Fact]
    public async Task EnsureLoadedAsync_LoadsOnceAndIsIdempotent()
    {
        var loadCount = 0;
        var fake = new FakeDoc2kbApiService();
        fake.OnGetQuality = (_, _) =>
        {
            loadCount++;
            return Task.FromResult(new QualityReport
            {
                TotalDocuments = 10,
                TotalChunks = 100,
                FormatDistribution = new Dictionary<string, int> { { "md", 5 }, { "pdf", 5 } },
                Warnings = new[] { "文档「large.pdf」体积较大" },
            });
        };
        fake.OnGetStats = (_, _) =>
        {
            return Task.FromResult(new Stats
            {
                TotalDocuments = 10,
                TotalChunks = 100,
                Collections = new Dictionary<string, int[]> { { "default", [10, 100, 500000] } },
            });
        };

        var vm = CreateVm(fake);

        // 首次加载
        await vm.EnsureLoadedAsync();
        Assert.Equal(1, loadCount);

        // 第二次幂等
        await vm.EnsureLoadedAsync();
        Assert.Equal(1, loadCount);

        // 数据已填充
        Assert.Single(vm.Warnings);
        Assert.Single(vm.Collections);
        Assert.Equal(10, vm.Stats!.TotalDocuments);
        Assert.Equal(100, vm.Stats.TotalChunks);
    }
}