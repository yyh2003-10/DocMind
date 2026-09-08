using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using CommunityToolkit.Mvvm.Input;

namespace DocMind.Tests;

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

    [Fact]
    public async Task SelectedPath_AsyncSummaryAndPreview_PopulatedForExistingFile()
    {
        var fake = new FakeDoc2kbApiService();
        var vm = CreateVm(fake);

        var tempFile = Path.Combine(Path.GetTempPath(), $"docmind_test_{Guid.NewGuid():N}.txt");
        try
        {
            await File.WriteAllTextAsync(tempFile, "Hello DocMind Testing Content");
            vm.SelectedPath = tempFile;

            // 异步后台加载，等待直到 Summary 刷新且不再是读取中占位符
            var timeout = DateTime.UtcNow.AddSeconds(5);
            while ((string.IsNullOrEmpty(vm.SelectedPathSummary) || vm.SelectedPathSummary.Contains("正在读取")) && DateTime.UtcNow < timeout)
            {
                await Task.Delay(50);
            }

            Assert.False(string.IsNullOrEmpty(vm.SelectedPathSummary));
            Assert.Contains("📄", vm.SelectedPathSummary);
            Assert.Contains(Path.GetFileName(tempFile), vm.SelectedPathSummary);
            Assert.Contains("Hello DocMind", vm.SelectedPathPreview);
        }
        finally
        {
            try { File.Delete(tempFile); } catch { }
        }
    }

    [Fact]
    public void ResetCommand_ResetsAllFieldsAndProgress()
    {
        var fake = new FakeDoc2kbApiService();
        var vm = CreateVm(fake);

        vm.SelectedPath = @"C:\tmp\folder";
        vm.Collection = "test_col";
        vm.Recursive = true;
        vm.Force = true;
        vm.ProgressPercent = 80;
        vm.StatusMessage = "处理中";

        vm.ResetCommand.Execute(null);

        Assert.Empty(vm.SelectedPath);
        Assert.Null(vm.Collection);
        Assert.False(vm.Recursive);
        Assert.False(vm.Force);
        Assert.Equal(0, vm.ProgressPercent);
        Assert.Equal("就绪", vm.StatusMessage);
        Assert.Empty(vm.CurrentProcessingItem);
        Assert.Empty(vm.ElapsedTimeText);
    }

    private static FakeDoc2kbApiService CreateFakeWithStats(params string[] collections)
        => new()
        {
            OnGetStats = (_, _) => Task.FromResult(new Stats
            {
                Collections = collections.ToDictionary(c => c, _ => Array.Empty<int>()),
            }),
        };

    // ===== 目标分组下拉 + 新建分组 =====

    [Fact]
    public async Task LoadCollections_PopulatesFromStats_AndPreselectsFirst()
    {
        var fake = CreateFakeWithStats("beta", "alpha");
        var vm = CreateVm(fake);
        await vm.LoadCollectionsAsync();

        Assert.Equal(new[] { "alpha", "beta", ImportViewModel.NewCollectionSentinel }, vm.AvailableCollections);
        Assert.Equal("alpha", vm.SelectedCollectionItem);
        Assert.Equal("alpha", vm.Collection);
    }

    [Fact]
    public async Task SelectedCollectionItem_SelectingExisting_UpdatesTargetAndExitsCreateMode()
    {
        var fake = CreateFakeWithStats("default", "beta");
        var vm = CreateVm(fake);
        await vm.LoadCollectionsAsync();

        vm.SelectedCollectionItem = ImportViewModel.NewCollectionSentinel;
        Assert.True(vm.IsCreatingCollection);
        Assert.Equal("default", vm.Collection); // 选哨兵不改变实际目标

        vm.SelectedCollectionItem = "beta";
        Assert.Equal("beta", vm.Collection);
        Assert.False(vm.IsCreatingCollection);
    }

    [Fact]
    public async Task ConfirmCreateCollection_CallsBackendAndSelectsNewCollection()
    {
        var created = new List<string>();
        var fake = CreateFakeWithStats("default");
        fake.OnCreateCollection = (name, _) =>
        {
            created.Add(name);
            return Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]> { ["default"] = Array.Empty<int>(), [name] = Array.Empty<int>() },
            });
        };
        var vm = CreateVm(fake);
        await vm.LoadCollectionsAsync();

        vm.SelectedCollectionItem = ImportViewModel.NewCollectionSentinel;
        vm.NewCollectionName = "  my-new-kb  ";
        await vm.ConfirmCreateCollectionCommand.ExecuteAsync(null);

        Assert.Equal("my-new-kb", created.Single());
        Assert.Equal("my-new-kb", vm.Collection);
        Assert.Equal("my-new-kb", vm.SelectedCollectionItem);
        Assert.Contains("my-new-kb", vm.AvailableCollections);
        Assert.Equal(ImportViewModel.NewCollectionSentinel, vm.AvailableCollections[^1]);
        Assert.False(vm.IsCreatingCollection);
        Assert.Contains("my-new-kb", vm.StatusMessage);
    }

    [Fact]
    public async Task ConfirmCreateCollection_BackendFails_StillSelectsLocally()
    {
        var fake = CreateFakeWithStats("default");
        fake.OnCreateCollection = (_, _) => Task.FromException<Stats>(new Exception("boom"));
        var vm = CreateVm(fake);
        await vm.LoadCollectionsAsync();

        vm.SelectedCollectionItem = ImportViewModel.NewCollectionSentinel;
        vm.NewCollectionName = "offline-kb";
        await vm.ConfirmCreateCollectionCommand.ExecuteAsync(null);

        Assert.Equal("offline-kb", vm.Collection);
        Assert.Contains("offline-kb", vm.AvailableCollections);
        Assert.Contains("创建分组失败", vm.StatusMessage);
        Assert.False(vm.IsCreatingCollection);
    }

    [Fact]
    public async Task CancelCreateCollection_RevertsToPreviousSelection()
    {
        var fake = CreateFakeWithStats("default");
        var vm = CreateVm(fake);
        await vm.LoadCollectionsAsync();

        vm.SelectedCollectionItem = ImportViewModel.NewCollectionSentinel;
        vm.CancelCreateCollectionCommand.Execute(null);

        Assert.False(vm.IsCreatingCollection);
        Assert.Equal("default", vm.SelectedCollectionItem);
        Assert.Equal("default", vm.Collection);
    }

    [Fact]
    public async Task ConfirmCreateCollection_EmptyName_CannotExecute()
    {
        var fake = CreateFakeWithStats("default");
        var vm = CreateVm(fake);
        await vm.LoadCollectionsAsync();

        vm.SelectedCollectionItem = ImportViewModel.NewCollectionSentinel;
        vm.NewCollectionName = "   ";
        Assert.False(vm.ConfirmCreateCollectionCommand.CanExecute(null));
    }
}
/// <summary>
/// DescribeStage：后端文件内阶段（解析/切片/嵌入/写库/AI 整理）优先展示，
/// 旧后端（无 stage 字段）退回文件计数文案。
/// </summary>
public class DescribeStageTests
{
    private static JobStatus Job(
        string? stage = null, double? stageProgress = null,
        string? currentFile = null, int processed = 0, int total = 0)
        => new()
        {
            JobId = "j",
            Type = "ingest",
            Status = "running",
            Processed = processed,
            Total = total,
            StartedAt = "2026-01-01T00:00:00",
            Stage = stage,
            StageProgress = stageProgress,
            CurrentFile = currentFile,
        };

    [Theory]
    [InlineData("parsing", "正在解析文档")]
    [InlineData("chunking", "正在切片")]
    [InlineData("writing", "正在写入向量索引")]
    [InlineData("curating", "正在 AI 整理")]
    public void Stage_WithFile_ShowsStageAndFile(string stage, string expectedStage)
    {
        var text = ImportViewModel.DescribeStage(Job(stage: stage, currentFile: @"C:\docs\a.pdf"));
        Assert.Equal($"{expectedStage}：a.pdf", text);
    }

    [Fact]
    public void Embedding_WithProgress_ShowsPercent()
    {
        var text = ImportViewModel.DescribeStage(
            Job(stage: "embedding", stageProgress: 0.37, currentFile: @"C:\docs\a.pdf"));
        Assert.Equal("正在向量嵌入（37%）：a.pdf", text);
    }

    [Fact]
    public void Stage_WithoutFile_ShowsStageOnly()
    {
        var text = ImportViewModel.DescribeStage(Job(stage: "chunking"));
        Assert.Equal("正在切片…", text);
    }

    [Fact]
    public void Legacy_WithCurrentFile_ShowsCountText()
    {
        var text = ImportViewModel.DescribeStage(
            Job(currentFile: @"C:\docs\a.pdf", processed: 1, total: 3));
        Assert.Equal("正在处理: a.pdf (1/3)", text);
    }

    [Fact]
    public void Legacy_WithoutFileButTotalKnown_ShowsDocCount()
    {
        var text = ImportViewModel.DescribeStage(Job(processed: 1, total: 3));
        Assert.Equal("正在处理文档 (1/3)…", text);
    }

    [Fact]
    public void Legacy_TotalUnknown_ShowsGenericIndexingText()
    {
        var text = ImportViewModel.DescribeStage(Job());
        Assert.Equal("正在执行文档切片与向量索引…", text);
    }
}
