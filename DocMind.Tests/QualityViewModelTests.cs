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
        => new(fake, new NotificationService());

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

    [Fact]
    public async Task EnsureLoadedAsync_RetriesAfterInitialFailure()
    {
        var attempts = 0;
        var fake = new FakeDoc2kbApiService
        {
            OnGetQuality = (_, _) =>
            {
                attempts++;
                if (attempts == 1)
                {
                    throw new BackendConnectionException("暂时不可达");
                }
                return Task.FromResult(new QualityReport
                {
                    TotalDocuments = 1,
                    TotalChunks = 2,
                });
            },
            OnGetStats = (_, _) => Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]> { { "default", [1, 2, 0] } },
            }),
        };

        var vm = CreateVm(fake);
        await vm.EnsureLoadedAsync();
        Assert.Contains("不可达", vm.StatusMessage);

        await vm.EnsureLoadedAsync();

        Assert.Equal(2, attempts);
        Assert.Equal("已更新", vm.StatusMessage);
    }
}