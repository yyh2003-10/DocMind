using System.Text.Json;
using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Tests;

/// <summary>质量看板「AI 知识库整理」（curate）流程测试：dry_run 预览 → 确认 → 执行。</summary>
public class QualityViewModelCurateTests
{
    private static QualityViewModel CreateVm(FakeDoc2kbApiService fake) => new(fake, new NotificationService());

    private static JobStatus CompletedCurateJob(bool dryRun) => new()
    {
        JobId = "job-1",
        Type = "curate",
        Status = "completed",
        Progress = 1.0,
        Report = JsonDocument.Parse(
            $$"""
            {
              "dry_run": {{(dryRun ? "true" : "false")}},
              "actions": ["enrich", "categorize", "dedup", "consolidate"],
              "enriched": [{}, {}],
              "categorized": [],
              "duplicates": [{}],
              "consolidated": [],
              "skipped": [],
              "errors": []
            }
            """
        ).RootElement,
    };

    /// <summary>提交任务返回 running，随后轮询返回终态；Capture 可选地记录最后发出的请求。</summary>
    private static FakeDoc2kbApiService FakeWithJob(CurateRequest?[]? captured, bool dryRun)
    {
        return new FakeDoc2kbApiService
        {
            OnCurate = (req, _) =>
            {
                if (captured is not null)
                {
                    captured[req.DryRun ? 0 : 1] = req;
                }
                return Task.FromResult(new JobStatus { JobId = "job-1", Type = "curate", Status = "running" });
            },
            OnGetJob = (_, _) => Task.FromResult(CompletedCurateJob(dryRun)),
        };
    }

    [Fact]
    public async Task PreviewCurate_SendsDryRunTrue_AndUnlocksExecute()
    {
        var captured = new CurateRequest?[2];
        var vm = CreateVm(FakeWithJob(captured, dryRun: true));

        await vm.PreviewCurateCommand.ExecuteAsync(null);

        Assert.NotNull(captured[0]);
        Assert.True(captured[0]!.DryRun);
        Assert.Contains("enrich", captured[0]!.Actions!);
        Assert.DoesNotContain("extract", captured[0]!.Actions!, StringComparer.Ordinal);
        // 预览完成后解锁「执行」按钮
        Assert.True(vm.ExecuteCurateCommand.CanExecute(null));
        Assert.Contains("打标签/摘要 2 篇", vm.CurateSummary);
        Assert.Contains("只读预览", vm.CurateSummary);
        Assert.Contains("预览完成", vm.CurateStatus);
    }

    [Fact]
    public async Task ExecuteCurate_SendsDryRunFalse_AfterPreview()
    {
        var captured = new CurateRequest?[2];
        var vm = CreateVm(FakeWithJob(captured, dryRun: false));

        await vm.PreviewCurateCommand.ExecuteAsync(null);
        Assert.True(captured[0]!.DryRun);

        await vm.ExecuteCurateCommand.ExecuteAsync(null);

        Assert.NotNull(captured[1]);
        Assert.False(captured[1]!.DryRun);
        Assert.Contains("已执行", vm.CurateSummary);
        Assert.Contains("整理执行完成", vm.CurateStatus);
    }

    [Fact]
    public void ExecuteCurate_DisabledBeforePreview()
    {
        var vm = CreateVm(new FakeDoc2kbApiService());
        Assert.False(vm.ExecuteCurateCommand.CanExecute(null));
        Assert.True(vm.PreviewCurateCommand.CanExecute(null));
    }

    [Fact]
    public async Task CurateFailure_SurfacesError_AndDoesNotUnlockExecute()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnCurate = (_, _) => Task.FromResult(new JobStatus { JobId = "job-1", Type = "curate", Status = "running" }),
            OnGetJob = (_, _) => Task.FromResult(new JobStatus
            {
                JobId = "job-1",
                Type = "curate",
                Status = "failed",
                Error = "LLM 不可用",
            }),
        };
        var vm = CreateVm(fake);

        await vm.PreviewCurateCommand.ExecuteAsync(null);

        Assert.Contains("LLM 不可用", vm.CurateStatus);
        Assert.False(vm.ExecuteCurateCommand.CanExecute(null));
        Assert.False(vm.IsCurating);
        Assert.True(string.IsNullOrEmpty(vm.CurateSummary));
    }

    [Fact]
    public async Task CollectionText_IsTrimmedAndPassedThrough()
    {
        var captured = new CurateRequest?[2];
        var vm = CreateVm(FakeWithJob(captured, dryRun: true));
        vm.Collection = "  papers  ";

        await vm.PreviewCurateCommand.ExecuteAsync(null);

        Assert.Equal("papers", captured[0]!.Collection);
    }

    [Fact]
    public async Task CollectionEmpty_MeansAllCollections()
    {
        var captured = new CurateRequest?[2];
        var vm = CreateVm(FakeWithJob(captured, dryRun: true));
        vm.Collection = "   ";

        await vm.PreviewCurateCommand.ExecuteAsync(null);

        Assert.Null(captured[0]!.Collection);
    }

    /// <summary>total 还没下发（刚提交/抢写锁）时：显示阶段文案 + 不确定进度条，而不是 0%。</summary>
    [Fact]
    public async Task Progress_BeforeTotalKnown_ShowsStageText_InsteadOfZeroPercent()
    {
        string? observedStatus = null;
        bool observedIndeterminate = false;
        QualityViewModel? holder = null;
        var fake = new FakeDoc2kbApiService
        {
            OnCurate = (_, _) => Task.FromResult(new JobStatus { JobId = "job-1", Type = "curate", Status = "running" }),
            OnWatchJobUntilDone = async (_, progress, _) =>
            {
                progress!.Report(new JobStatus
                {
                    JobId = "job-1",
                    Type = "curate",
                    Status = "running",
                    Progress = 0.0,
                    Total = 0,
                    Stage = "queued",
                });
                // Progress<T> 回调异步投递：轮询等它落地（最多 3s）
                for (var i = 0; i < 60; i++)
                {
                    if (holder!.CurateStatus.Contains("排队", StringComparison.Ordinal))
                    {
                        break;
                    }
                    await Task.Delay(50);
                }
                observedStatus = holder!.CurateStatus;
                observedIndeterminate = holder.IsCurateIndeterminate;
                return CompletedCurateJob(dryRun: true);
            },
        };
        var vm = CreateVm(fake);
        holder = vm;

        await vm.PreviewCurateCommand.ExecuteAsync(null);

        Assert.NotNull(observedStatus);
        Assert.Contains("排队等待写入锁", observedStatus);
        Assert.DoesNotContain("0%", observedStatus);
        Assert.True(observedIndeterminate);
        // 终态后回到确定进度
        Assert.False(vm.IsCurateIndeterminate);
        Assert.Contains("预览完成", vm.CurateStatus);
    }

    /// <summary>前端跟踪中断（watch 抛异常）后回到看板：按 job_id 续接到终态并补齐报告。</summary>
    [Fact]
    public async Task ResumeCurateWatch_ReattachesAfterWatchInterruption()
    {
        var watchCalls = 0;
        var fake = new FakeDoc2kbApiService
        {
            OnCurate = (_, _) => Task.FromResult(new JobStatus { JobId = "job-1", Type = "curate", Status = "running" }),
            OnWatchJobUntilDone = (_, _, _) =>
            {
                if (Interlocked.Increment(ref watchCalls) == 1)
                {
                    // 模拟 SSE/轮询中断：跟踪协程抛异常，但后端 job 仍在跑
                    throw new IOException("SSE interrupted");
                }
                return Task.FromResult(CompletedCurateJob(dryRun: true));
            },
            OnGetJob = (_, _) => Task.FromResult(new JobStatus { JobId = "job-1", Type = "curate", Status = "running" }),
        };
        var vm = CreateVm(fake);

        await vm.PreviewCurateCommand.ExecuteAsync(null);
        // 首次跟踪失败：状态为错误，但 job_id 保留（finally 不清）
        Assert.Contains("错误", vm.CurateStatus);
        Assert.False(vm.IsCurating);

        await vm.ResumeCurateWatchAsync();

        Assert.Equal(2, watchCalls);
        Assert.Contains("预览完成", vm.CurateStatus);
        Assert.True(vm.ExecuteCurateCommand.CanExecute(null));
        Assert.False(vm.IsCurating);
    }

    /// <summary>job 已在后端完成但前端错过结果：续接时拉一次报告补齐展示。</summary>
    [Fact]
    public async Task ResumeCurateWatch_JobAlreadyCompleted_FillsReportFromSnapshot()
    {
        var fake = new FakeDoc2kbApiService
        {
            OnCurate = (_, _) => Task.FromResult(new JobStatus { JobId = "job-1", Type = "curate", Status = "running" }),
            OnWatchJobUntilDone = (_, _, _) => throw new IOException("SSE interrupted"),
            OnGetJob = (_, _) => Task.FromResult(CompletedCurateJob(dryRun: true)),
        };
        var vm = CreateVm(fake);

        await vm.PreviewCurateCommand.ExecuteAsync(null);
        await vm.ResumeCurateWatchAsync();

        Assert.Contains("预览完成", vm.CurateStatus);
        Assert.Contains("打标签/摘要 2 篇", vm.CurateSummary);
        Assert.True(vm.ExecuteCurateCommand.CanExecute(null));
    }
}