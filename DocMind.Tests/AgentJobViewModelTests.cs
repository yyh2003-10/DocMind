using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Tests;

[Collection("SettingsFile")]
public sealed class AgentJobViewModelTests : IDisposable
{
    private readonly string _checkpointDirectory;
    private readonly CheckpointService _checkpoint;

    public AgentJobViewModelTests()
    {
        _checkpointDirectory = Path.Combine(Path.GetTempPath(), $"docmind_agent_jobs_{Guid.NewGuid():N}");
        _checkpoint = new CheckpointService(_checkpointDirectory);
    }

    public void Dispose()
    {
        try { Directory.Delete(_checkpointDirectory, recursive: true); } catch { }
    }

    private static FakeDoc2kbApiService CreateFake()
    {
        return new FakeDoc2kbApiService
        {
            OnGetStats = (_, _) => Task.FromResult(new Stats
            {
                Collections = new Dictionary<string, int[]> { ["default"] = [0, 0, 0] },
            }),
        };
    }

    private ChatViewModel CreateVm(FakeDoc2kbApiService fake)
    {
        var settings = new AppSettings
        {
            LlmProvider = "openai",
            LlmApiKey = "test-key",
            AgentModeEnabled = true,
            LastChatMode = "agent",
        };
        return new ChatViewModel(fake, new NotificationService(), settings, checkpoint: _checkpoint);
    }

    private static JobStatus CompletedJob(string jobId = "agent-job-1") => new()
    {
        JobId = jobId,
        Type = "agent",
        Status = "completed",
        Progress = 1.0,
        Stage = "completed",
        Result = new AgentJobResult
        {
            Answer = "Durable answer",
            ChatId = "chat-agent-1",
            Model = "test-model",
            Provider = "test-provider",
            ElapsedMs = 42,
            Sources = [new SourceRef { Index = 1, Source = "notes.md", Format = "md" }],
            Trajectory = [new AgentTrajectoryStep { Step = 1, Type = "tool_result", ToolId = "kb_search", Status = "ok" }],
            TrajectoryWarning = "Recovered after reconnect",
        },
    };

    private static async Task WaitUntilAsync(Func<bool> condition, int timeoutMs = 1500)
    {
        var deadline = Environment.TickCount64 + timeoutMs;
        while (!condition())
        {
            if (Environment.TickCount64 >= deadline)
            {
                throw new TimeoutException("Expected ViewModel state was not reached.");
            }
            await Task.Delay(10);
        }
    }

    [Fact]
    public async Task StartAgentJob_SubmitsAgentRequestAndAppliesTerminalResult()
    {
        var fake = CreateFake();
        ChatRequest? captured = null;
        fake.OnStartAgentJob = (request, _) =>
        {
            captured = request;
            return Task.FromResult(new JobStatus { JobId = "agent-job-1", Type = "agent", Status = "running" });
        };
        fake.OnWatchJobUntilDone = (_, progress, _) =>
        {
            progress?.Report(new JobStatus { JobId = "agent-job-1", Type = "agent", Status = "running", Progress = 0.4, Stage = "running_tool" });
            return Task.FromResult(CompletedJob());
        };
        var vm = CreateVm(fake);
        vm.SelectedChatMode = ChatModeChoice.Agent;
        vm.InputText = "Build an execution plan";

        await vm.StartAgentJobCommand.ExecuteAsync(null);
        await WaitUntilAsync(() => !vm.IsAgentJobRunning && vm.Messages.Any(message => message.Content == "Durable answer"));

        Assert.NotNull(captured);
        Assert.True(captured.AgentMode);
        Assert.Equal("agent", captured.ChatMode);
        var assistant = vm.Messages.Single(message => message.Content == "Durable answer");
        Assert.Equal("Durable answer", assistant.Content);
        Assert.Equal("test-model", assistant.Model);
        Assert.Equal("test-provider", assistant.Provider);
        Assert.True(assistant.HasSources, DescribeMessages(vm));
        Assert.NotEmpty(assistant.TrajectorySteps!);
        Assert.Equal("Recovered after reconnect", assistant.TrajectoryWarning);
        Assert.Null(await _checkpoint.RestoreCheckpointAsync("agent-job-1"));
    }

    [Fact]
    public async Task StopAgentJob_CallsBackendCancellationAndMarksPlaceholderPartial()
    {
        var fake = CreateFake();
        var completion = new TaskCompletionSource<JobStatus>(TaskCreationOptions.RunContinuationsAsynchronously);
        var cancelCalled = false;
        fake.OnStartAgentJob = (_, _) => Task.FromResult(new JobStatus { JobId = "agent-job-stop", Type = "agent", Status = "running" });
        fake.OnWatchJobUntilDone = (_, _, _) => completion.Task;
        fake.OnCancelJob = (jobId, _) =>
        {
            cancelCalled = true;
            var cancelled = new JobStatus { JobId = jobId, Type = "agent", Status = "cancelled", Stage = "cancelled" };
            completion.TrySetResult(cancelled);
            return Task.FromResult(cancelled);
        };
        var vm = CreateVm(fake);
        vm.SelectedChatMode = ChatModeChoice.Agent;
        vm.InputText = "Run a long task";

        await vm.StartAgentJobCommand.ExecuteAsync(null);
        await WaitUntilAsync(() => vm.IsAgentJobRunning);
        vm.StopCommand.Execute(null);
        await WaitUntilAsync(() => cancelCalled && !vm.IsAgentJobRunning);

        var assistant = vm.Messages.Last(message => message.Role == "assistant");
        Assert.True(assistant.Partial, DescribeMessages(vm));
        Assert.NotNull(assistant.TrajectoryWarning);
    }

    [Fact]
    public async Task Constructor_ReattachesAgentCheckpointAndRestoresResult()
    {
        await _checkpoint.SaveCheckpointAsync(new CheckpointState
        {
            OperationId = "agent-job-resume",
            OperationType = "agent",
            Metadata = new Dictionary<string, string> { ["chatId"] = "chat-resume", ["query"] = "Resume task" },
        });
        var fake = CreateFake();
        fake.OnWatchJobUntilDone = (_, _, _) => Task.FromResult(CompletedJob("agent-job-resume"));

        var vm = CreateVm(fake);

        await WaitUntilAsync(() => vm.Messages.Any(message => message.Content == "Durable answer"));
        Assert.False(vm.IsAgentJobRunning);
        Assert.Null(await _checkpoint.RestoreCheckpointAsync("agent-job-resume"));
    }

    private static string DescribeMessages(ChatViewModel vm) => string.Join(
        " | ",
        vm.Messages.Select(message => $"{message.Role}:{message.Content}:sources={message.Sources?.Count ?? 0}:partial={message.Partial}:model={message.Model}"));
}
