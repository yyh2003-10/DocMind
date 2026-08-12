using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using CommunityToolkit.Mvvm.Input;

namespace WpfApp1.Tests;

/// <summary>
/// ImportViewModel 异步 job 导入：真实进度递增、结果明细、取消行为。
/// 只测外部行为（公开状态），不测实现细节。
/// </summary>
public class ImportViewModelTests
{
    private static ImportViewModel CreateVm(FakeDoc2kbApiService fake)
        => new(fake, new NotificationService());

    private static JobStatus RunningJob(string jobId, int processed, int total)
        => new()
        {
            JobId = jobId,
            Type = "ingest",
            Status = "running",
            Progress = total == 0 ? 0 : (double)processed / total,
            Processed = processed,
            Total = total,
            StartedAt = "2026-01-01T00:00:00",
        };

    private static JobStatus CompletedJob(string jobId, IReadOnlyList<IngestResult> results)
        => new()
        {
            JobId = jobId,
            Type = "ingest",
            Status = "completed",
            Progress = 1.0,
            Processed = results.Count,
            Total = results.Count,
            StartedAt = "2026-01-01T00:00:00",
            FinishedAt = "2026-01-01T00:01:00",
            Results = results,
        };

    [Fact]
    public async Task ImportAsync_WithProgress_ReportsMonotonicPercentUntil100()
    {
        var fake = new FakeDoc2kbApiService();
        var jobId = "job-1";
        var observed = new List<int>();

        fake.OnIngestJob = (_, _) => Task.FromResult(RunningJob(jobId, 0, 3));
        // 轮询：第一次 running(1/3)，第二次 running(2/3)，第三次 completed
        var poll = 0;
        fake.OnGetJob = (_, _) =>
        {
            poll++;
            return Task.FromResult(poll switch
            {
                1 => RunningJob(jobId, 1, 3),
                2 => RunningJob(jobId, 2, 3),
                _ => CompletedJob(jobId, new[]
                {
                    new IngestResult { Source = "a.md", Status = "ingested", ChunkCount = 5 },
                    new IngestResult { Source = "b.md", Status = "ingested", ChunkCount = 3 },
                    new IngestResult { Source = "c.md", Status = "ingested", ChunkCount = 2 },
                }),
            });
        };

        var vm = CreateVm(fake);
        vm.SelectedPath = @"C:\tmp\folder";
        vm.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(ImportViewModel.ProgressPercent))
                observed.Add(vm.ProgressPercent);
        };

        await vm.ImportCommand.ExecuteAsync(null);

        // 进度单调递增且最终到 100
        Assert.NotEmpty(observed);
        Assert.Equal(100, observed[^1]);
        for (var i = 1; i < observed.Count; i++)
            Assert.True(observed[i] >= observed[i - 1]);

        // 结果三栏正确
        Assert.Equal(3, vm.Results.Count);
        Assert.Empty(vm.Skipped);
        Assert.Empty(vm.Failed);
        Assert.True(vm.Results.All(r => r.Status == "ingested"));
        Assert.False(vm.IsBusy);
    }

    [Fact]
    public async Task ImportAsync_WithFailures_PopulatesFailedColumnWithReason()
    {
        var fake = new FakeDoc2kbApiService();
        var jobId = "job-2";
        fake.OnIngestJob = (_, _) => Task.FromResult(RunningJob(jobId, 0, 2));
        fake.OnGetJob = (_, _) => Task.FromResult(CompletedJob(jobId, new[]
        {
            new IngestResult { Source = "ok.md", Status = "ingested", ChunkCount = 4 },
            new IngestResult { Source = "bad.pdf", Status = "failed", Error = "加载失败: 损坏文件" },
        }));

        var vm = CreateVm(fake);
        vm.SelectedPath = @"C:\tmp\folder";

        await vm.ImportCommand.ExecuteAsync(null);

        Assert.Single(vm.Results);
        Assert.Single(vm.Failed);
        Assert.Contains("bad.pdf", vm.Failed[0]);
        Assert.Contains("加载失败", vm.Failed[0]);
        Assert.Contains("完成：导入 1", vm.StatusMessage);
    }

    [Fact]
    public async Task ImportAsync_WithSkipped_PopulatesSkippedColumn()
    {
        var fake = new FakeDoc2kbApiService();
        var jobId = "job-3";
        fake.OnIngestJob = (_, _) => Task.FromResult(RunningJob(jobId, 0, 2));
        fake.OnGetJob = (_, _) => Task.FromResult(CompletedJob(jobId, new[]
        {
            new IngestResult { Source = "dup.md", Status = "skipped" },
            new IngestResult { Source = "new.md", Status = "ingested", ChunkCount = 7 },
        }));

        var vm = CreateVm(fake);
        vm.SelectedPath = @"C:\tmp\folder";

        await vm.ImportCommand.ExecuteAsync(null);

        Assert.Single(vm.Results);
        Assert.Contains(vm.Skipped, s => s.Contains("dup.md"));
    }

    [Fact]
    public async Task ImportAsync_JobFailed_SetsErrorStatus()
    {
        var fake = new FakeDoc2kbApiService();
        var jobId = "job-4";
        fake.OnIngestJob = (_, _) => Task.FromResult(RunningJob(jobId, 0, 5));
        fake.OnGetJob = (_, _) => Task.FromResult(new JobStatus
        {
            JobId = jobId,
            Type = "ingest",
            Status = "failed",
            Error = "嵌入模型加载失败",
            StartedAt = "2026-01-01T00:00:00",
            FinishedAt = "2026-01-01T00:00:05",
        });

        var vm = CreateVm(fake);
        vm.SelectedPath = @"C:\tmp\folder";

        await vm.ImportCommand.ExecuteAsync(null);

        Assert.Contains("嵌入模型加载失败", vm.StatusMessage);
        Assert.Contains(vm.Failed, f => f.Contains("任务失败"));
        Assert.False(vm.IsBusy);
    }

    [Fact]
    public async Task ImportAsync_Cancellation_StopsPollingAndMarksCancelled()
    {
        var fake = new FakeDoc2kbApiService();
        var jobId = "job-5";
        fake.OnIngestJob = (_, _) => Task.FromResult(RunningJob(jobId, 0, 10));
        // 每次轮询都返回 running，永不完成 → 测试取消路径
        fake.OnGetJob = (id, ct) =>
        {
            ct.ThrowIfCancellationRequested();
            return Task.FromResult(RunningJob(jobId, 5, 10));
        };

        var vm = CreateVm(fake);
        vm.SelectedPath = @"C:\tmp\folder";

        // 启动导入（不 await，模拟进行中）
        var importTask = vm.ImportCommand.ExecuteAsync(null);
        // 等待轮询至少跑过一次
        await Task.Delay(100);
        // 取消
        vm.CancelImportCommand.Execute(null);

        await importTask;

        Assert.False(vm.IsBusy);
        Assert.Contains("已取消", vm.StatusMessage);
    }

    [Fact]
    public void ImportCommand_Disabled_WhenBusyOrNoPath()
    {
        var fake = new FakeDoc2kbApiService();
        var vm = CreateVm(fake);

        // 无路径不可执行
        Assert.False(vm.ImportCommand.CanExecute(null));

        vm.SelectedPath = @"C:\tmp\folder";
        Assert.True(vm.ImportCommand.CanExecute(null));
    }
}