using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind.Tests;

[Collection("SettingsFile")]
public class TaskProgressTests
{
    [Fact]
    public void TaskProgress_AggregatesMessageEvidenceToolsAndArtifactState()
    {
        var fakeApi = new FakeDoc2kbApiService();
        fakeApi.OnGetStats = (_, _) => Task.FromResult(new Stats
        {
            Collections = new Dictionary<string, int[]> { ["default"] = [0, 0, 0] },
        });
        var vm = new ChatViewModel(fakeApi, null, new AppSettings
        {
            LlmProvider = "openai",
            LlmApiKey = "test-key",
        });
        vm.TaskGoal = "Create a decision-ready research brief";

        var beforeEvidence = vm.TaskProgressNextAction;
        var message = new ChatMessage { Role = "assistant" };
        vm.Messages.Add(message);
        message.Sources =
        [
            new SourceRef { Source = "local.md", ChunkId = 7 },
            new SourceRef
            {
                Source = "https://example.com/a",
                SourceType = "web",
                Url = "https://example.com/a",
                ContentFetched = true,
            },
        ];

        Assert.Equal(2, vm.TaskSourceCount);
        Assert.Equal(1, vm.TaskLocalSourceCount);
        Assert.Equal(1, vm.TaskWebSourceCount);
        Assert.NotEqual(beforeEvidence, vm.TaskProgressNextAction);

        message.TrajectorySteps =
        [
            new AgentTrajectoryStep { Step = 1, Type = "tool_result", ToolId = "search", Status = "ok" },
            new AgentTrajectoryStep { Step = 2, Type = "tool_result", ToolId = "fetch", Status = "error" },
        ];

        Assert.Equal(2, vm.TaskToolStepCount);
        Assert.Equal(1, vm.TaskToolIssueCount);

        var afterToolIssue = vm.TaskProgressNextAction;
        message.TrajectoryWarning = "工具调用次数已达上限";

        Assert.True(vm.HasTaskAgentWarning);
        Assert.Equal(1, vm.TaskAgentWarningCount);
        Assert.Contains("恢复提示", vm.TaskProgressToolText);
        Assert.Contains("重新执行", vm.TaskProgressNextAction);

        message.TrajectoryWarning = null;
        message.TrajectorySteps = [];
        message.Artifact = new ArtifactItem
        {
            Title = "Research brief",
            Type = "docx",
            LifecycleState = ArtifactLifecycleState.ExportFailed,
            LatestVersion = 2,
        };

        Assert.Equal(1, vm.TaskArtifactCount);
        Assert.Same(message.Artifact, vm.LatestTaskArtifact);
        Assert.NotEqual(afterToolIssue, vm.TaskProgressNextAction);
        Assert.Contains("v2", vm.TaskProgressArtifactText);
    }

    [Fact]
    public void ToggleTaskProgressPanelCommand_TogglesPanelState()
    {
        var vm = new ChatViewModel(new FakeDoc2kbApiService());

        Assert.False(vm.IsTaskProgressPanelOpen);
        vm.ToggleTaskProgressPanelCommand.Execute(null);
        Assert.True(vm.IsTaskProgressPanelOpen);
    }

    [Fact]
    public void PrepareTaskContinuationCommand_QueuesTaskAwarePrompts()
    {
        var vm = new ChatViewModel(new FakeDoc2kbApiService());
        vm.TaskGoal = "Build a research brief";
        vm.TaskDeliverable = "DOCX";

        vm.PrepareTaskContinuationCommand.Execute("evidence");
        var evidencePrompt = vm.InputText;

        vm.PrepareTaskContinuationCommand.Execute("convert");

        Assert.NotEmpty(evidencePrompt);
        Assert.NotEmpty(vm.InputText);
        Assert.NotEqual(evidencePrompt, vm.InputText);
        Assert.Contains("DOCX", vm.InputText);

        vm.PrepareTaskContinuationCommand.Execute("retry");

        Assert.Contains("已完成的工具步骤", vm.InputText);
    }

    [Fact]
    public void OpenTaskSettlementCommand_PrefillsTaskResultAndSourcesWithoutWriting()
    {
        var vm = new ChatViewModel(new FakeDoc2kbApiService());
        vm.TaskGoal = "Capture reusable findings";
        var message = new ChatMessage
        {
            Role = "assistant",
            Content = "Reusable result",
            Artifact = new ArtifactItem
            {
                Title = "Research result",
                Type = "docx",
                RawContent = "Artifact body",
            },
            Sources = [new SourceRef { Source = "local.md", ChunkId = 3 }],
        };
        vm.Messages.Add(message);

        vm.OpenTaskSettlementCommand.Execute(null);

        Assert.True(vm.IsIngestDialogOpen);
        Assert.Contains("Artifact body", vm.IngestDialogContent);
        Assert.Contains("local.md", vm.IngestDialogContent);
        Assert.Equal("Capture reusable findings", vm.IngestDialogTitle);
    }
}
