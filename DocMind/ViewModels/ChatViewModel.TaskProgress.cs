using System.Collections.Specialized;
using System.ComponentModel;
using DocMind.Models;

namespace DocMind.ViewModels;

public partial class ChatViewModel
{
    private readonly HashSet<ChatMessage> _taskProgressObservedMessages = [];
    private bool _isTaskProgressPanelOpen;

    public bool IsTaskProgressPanelOpen
    {
        get => _isTaskProgressPanelOpen;
        set => SetProperty(ref _isTaskProgressPanelOpen, value);
    }

    public bool HasTaskProgress => HasTaskContext || Messages.Count > 0;

    public string TaskProgressGoalText => string.IsNullOrWhiteSpace(TaskGoal)
        ? "未设置目标"
        : TaskGoal.Trim();

    public int TaskSourceCount => AllTaskSources()
        .Select(SourceIdentity)
        .Distinct(StringComparer.OrdinalIgnoreCase)
        .Count();

    public int TaskLocalSourceCount => AllTaskSources()
        .Where(source => !source.IsWebSource)
        .Select(SourceIdentity)
        .Distinct(StringComparer.OrdinalIgnoreCase)
        .Count();

    public int TaskWebSourceCount => AllTaskSources()
        .Where(source => source.IsWebSource)
        .Select(SourceIdentity)
        .Distinct(StringComparer.OrdinalIgnoreCase)
        .Count();

    public int TaskToolStepCount => Messages
        .SelectMany(message => message.TrajectorySteps ?? [])
        .Count();

    public int TaskToolIssueCount => Messages
        .SelectMany(message => message.TrajectorySteps ?? [])
        .Count(step => string.Equals(step.Status, "error", StringComparison.OrdinalIgnoreCase)
            || string.Equals(step.Status, "denied", StringComparison.OrdinalIgnoreCase));

    public int TaskAgentWarningCount => Messages.Count(message => message.HasTrajectoryWarning);

    public bool HasTaskAgentWarning => TaskAgentWarningCount > 0;

    public int TaskArtifactCount => Messages
        .Select(message => message.Artifact)
        .Where(artifact => artifact is not null)
        .Distinct()
        .Count();

    public bool HasTaskArtifact => LatestTaskArtifact is not null;

    public bool HasTaskResultToSettle => LatestTaskArtifact is not null || LatestTaskOutput is not null;

    public ArtifactItem? LatestTaskArtifact => Messages
        .Select(message => message.Artifact)
        .LastOrDefault(artifact => artifact is not null);

    public string TaskProgressEvidenceText => TaskSourceCount switch
    {
        0 => "尚未收集可回溯来源",
        _ => $"{TaskSourceCount} 条来源（本地 {TaskLocalSourceCount}，网页 {TaskWebSourceCount}）",
    };

    public string TaskProgressToolText => TaskToolStepCount switch
    {
        0 when TaskAgentWarningCount > 0 => $"本轮未完整收尾，{TaskAgentWarningCount} 条恢复提示",
        0 => "未使用 Agent 工具",
        _ when TaskToolIssueCount > 0 && TaskAgentWarningCount > 0 => $"{TaskToolStepCount} 个工具步骤，{TaskToolIssueCount} 个待处理，{TaskAgentWarningCount} 条恢复提示",
        _ when TaskToolIssueCount > 0 => $"{TaskToolStepCount} 个工具步骤，{TaskToolIssueCount} 个待处理",
        _ when TaskAgentWarningCount > 0 => $"{TaskToolStepCount} 个工具步骤，{TaskAgentWarningCount} 条恢复提示",
        _ => $"{TaskToolStepCount} 个工具步骤已记录",
    };

    public string TaskProgressArtifactText
    {
        get
        {
            var artifact = LatestTaskArtifact;
            if (artifact is null)
            {
                return string.IsNullOrWhiteSpace(TaskDeliverable)
                    ? "尚未生成交付物"
                    : $"待生成：{TaskDeliverable.Trim()}";
            }

            var version = artifact.LatestVersion is > 0 ? $" · v{artifact.LatestVersion}" : string.Empty;
            return $"{TaskArtifactCount} 个交付物，当前{artifact.LifecycleText}{version}";
        }
    }

    public string TaskProgressNextAction
    {
        get
        {
            var artifact = LatestTaskArtifact;
            if (!HasTaskContext)
            {
                return "补充任务目标或交付物，让后续生成保持一致。";
            }
            if (TaskAgentWarningCount > 0)
            {
                return "Agent 本轮在失败、预算或取消处收尾；可基于已完成步骤重新执行，或先重检索。";
            }
            if (TaskSourceCount == 0)
            {
                return "先检索或补充资料，再形成可追溯结论。";
            }
            if (TaskToolIssueCount > 0)
            {
                return "查看失败或被拒绝的工具步骤，确认是否需要重试。";
            }
            if (artifact is null)
            {
                return "基于已收集来源生成交付物草稿。";
            }

            return artifact.LifecycleState switch
            {
                ArtifactLifecycleState.Draft when artifact.IsPpt => "运行效果自检，确认演示文稿结构与密度。",
                ArtifactLifecycleState.Draft => "检查草稿内容后导出，或继续修订。",
                ArtifactLifecycleState.Checked => "根据检查结果修订，或直接导出交付。",
                ArtifactLifecycleState.ExportFailed => "查看导出错误，修订后重新导出。",
                ArtifactLifecycleState.Exported => "交付已导出；可继续修订或沉淀为知识。",
                _ => "继续推进当前任务。",
            };
        }
    }

    [CommunityToolkit.Mvvm.Input.RelayCommand]
    private void ToggleTaskProgressPanel() => IsTaskProgressPanelOpen = !IsTaskProgressPanelOpen;

    /// <summary>
    /// 将常见的研究续作编译为可编辑草稿。实际发送仍走标准对话链路，
    /// 因而会沿用本会话的任务规格、资料范围和引用约束。
    /// </summary>
    [CommunityToolkit.Mvvm.Input.RelayCommand]
    private void PrepareTaskContinuation(string? action)
    {
        if (IsBusy)
        {
            return;
        }

        var artifact = LatestTaskArtifact;
        var prompt = action?.Trim().ToLowerInvariant() switch
        {
            "evidence" => "请围绕当前研究任务补充关键结论的可回溯证据。标注单一来源、证据冲突和仍缺少的资料；不要编造引用。",
            "research" => "请围绕当前研究任务重新检索已选资料，优先补足证据缺口；只保留可回溯来源，并说明新增或变化的结论。",
            "retry" => "请基于当前任务、已收集来源和已完成的工具步骤继续完成任务。不要重复已成功的操作；先说明从哪一步恢复，并在必要时重新检索或补充证据。",
            "revise" when artifact is not null => $"请基于当前任务规格，修订交付物《{artifact.Title}》。保留已有可用结论与引用，先说明修改重点，再输出可直接替换的完整草稿。",
            "revise" => "请基于当前任务规格，先给出交付物草稿的修改方案，再输出完整的修订稿。",
            "convert" when !string.IsNullOrWhiteSpace(TaskDeliverable) => $"请将当前研究成果整理为 {TaskDeliverable.Trim()}。保留可回溯引用，并按当前任务规格输出可交付的完整内容。",
            "convert" => "请将当前研究成果转换为目标交付格式。保留可回溯引用，并说明建议的格式与结构。",
            _ => string.Empty,
        };

        if (string.IsNullOrWhiteSpace(prompt))
        {
            return;
        }

        InputText = prompt;
        StatusMessage = "已生成连续操作指令，可补充要求后发送";
    }

    [CommunityToolkit.Mvvm.Input.RelayCommand(CanExecute = nameof(CanExportLatestTaskArtifact))]
    private async Task ExportLatestTaskArtifactAsync()
    {
        var artifact = LatestTaskArtifact;
        if (artifact is null)
        {
            return;
        }

        OpenArtifact(artifact);
        await ExportArtifactFileAsync();
    }

    private bool CanExportLatestTaskArtifact() => !IsBusy && LatestTaskArtifact is not null;

    /// <summary>
    /// 将当前任务的规格、最新成果和可回溯来源预填到已有的入库确认弹窗。
    /// 这里绝不直接写库，用户仍可删改内容、选择集合后再确认。
    /// </summary>
    [CommunityToolkit.Mvvm.Input.RelayCommand]
    private void OpenTaskSettlement()
    {
        var artifact = LatestTaskArtifact;
        var output = LatestTaskOutput;
        if (artifact is null && output is null)
        {
            return;
        }

        var builder = new System.Text.StringBuilder();
        var title = !string.IsNullOrWhiteSpace(TaskGoal)
            ? TaskGoal.Trim()
            : artifact?.Title ?? "研究任务沉淀";

        builder.AppendLine($"# {title}");
        builder.AppendLine();
        builder.AppendLine("## 任务规格");
        AppendTaskField(builder, "目标", TaskGoal);
        AppendTaskField(builder, "受众", TaskAudience);
        AppendTaskField(builder, "交付物", TaskDeliverable);
        AppendTaskField(builder, "引用规范", TaskCitationStyle);
        AppendTaskField(builder, "约束", TaskConstraints);

        builder.AppendLine();
        builder.AppendLine("## 本次成果");
        if (artifact is not null)
        {
            builder.AppendLine($"交付物：{artifact.Title}（{artifact.Type.ToUpperInvariant()}，{artifact.LifecycleText}）");
            builder.AppendLine();
            builder.AppendLine(artifact.RawContent.Trim());
        }
        else if (output is not null)
        {
            builder.AppendLine(output.Content.Trim());
        }

        var sources = AllTaskSources()
            .GroupBy(SourceIdentity, StringComparer.OrdinalIgnoreCase)
            .Select(group => group.First())
            .Take(20)
            .ToList();
        if (sources.Count > 0)
        {
            builder.AppendLine();
            builder.AppendLine("## 来源回溯");
            foreach (var source in sources)
            {
                if (source.IsWebSource)
                {
                    builder.AppendLine($"- 网页：{source.Title ?? source.Source}（{source.Url ?? source.Source}）");
                    continue;
                }

                var location = source.Page is > 0
                    ? $"第 {source.Page} 页"
                    : source.ChunkId is > 0 ? $"分块 {source.ChunkId}" : null;
                builder.AppendLine($"- 本地：{source.Source}{(location is null ? string.Empty : $"（{location}）")}");
            }
        }
        else
        {
            builder.AppendLine();
            builder.AppendLine("## 来源回溯");
            builder.AppendLine("- 本次任务尚未形成可回溯来源；提交前请补充或删减相关结论。");
        }

        _currentIngestingMessage = null;
        IngestDialogTitle = title.Length > 60 ? title[..60] : title;
        IngestDialogContent = builder.ToString().Trim();
        IngestDialogTags = string.IsNullOrWhiteSpace(TaskDeliverable)
            ? "研究任务, 任务沉淀"
            : $"研究任务, 任务沉淀, {TaskDeliverable.Trim()}";
        IngestDialogCollection = SelectedCollections.FirstOrDefault() ?? "default";
        IsIngestDialogOpen = true;
        StatusMessage = "已预填任务成果，确认内容后可沉淀入库";
    }

    private void InitializeTaskProgressTracking()
    {
        Messages.CollectionChanged += OnTaskMessagesCollectionChanged;
        foreach (var message in Messages)
        {
            ObserveTaskProgressMessage(message);
        }
        RefreshTaskProgress();
    }

    private void OnTaskMessagesCollectionChanged(object? sender, NotifyCollectionChangedEventArgs eventArgs)
    {
        if (eventArgs.OldItems is not null)
        {
            foreach (ChatMessage message in eventArgs.OldItems)
            {
                StopObservingTaskProgressMessage(message);
            }
        }
        if (eventArgs.NewItems is not null)
        {
            foreach (ChatMessage message in eventArgs.NewItems)
            {
                ObserveTaskProgressMessage(message);
            }
        }
        RefreshTaskProgress();
    }

    private void ObserveTaskProgressMessage(ChatMessage message)
    {
        if (_taskProgressObservedMessages.Add(message))
        {
            message.PropertyChanged += OnTaskProgressMessagePropertyChanged;
        }
    }

    private void StopObservingTaskProgressMessage(ChatMessage message)
    {
        if (_taskProgressObservedMessages.Remove(message))
        {
            message.PropertyChanged -= OnTaskProgressMessagePropertyChanged;
        }
    }

    private void OnTaskProgressMessagePropertyChanged(object? sender, PropertyChangedEventArgs eventArgs)
    {
        if (eventArgs.PropertyName is nameof(ChatMessage.Sources)
            or nameof(ChatMessage.TrajectorySteps)
            or nameof(ChatMessage.TrajectoryWarning)
            or nameof(ChatMessage.Artifact))
        {
            RefreshTaskProgress();
        }
    }

    private void RefreshTaskProgress()
    {
        OnPropertyChanged(nameof(HasTaskProgress));
        OnPropertyChanged(nameof(TaskProgressGoalText));
        OnPropertyChanged(nameof(TaskSourceCount));
        OnPropertyChanged(nameof(TaskLocalSourceCount));
        OnPropertyChanged(nameof(TaskWebSourceCount));
        OnPropertyChanged(nameof(TaskToolStepCount));
        OnPropertyChanged(nameof(TaskToolIssueCount));
        OnPropertyChanged(nameof(TaskAgentWarningCount));
        OnPropertyChanged(nameof(HasTaskAgentWarning));
        OnPropertyChanged(nameof(TaskArtifactCount));
        OnPropertyChanged(nameof(HasTaskArtifact));
        OnPropertyChanged(nameof(HasTaskResultToSettle));
        OnPropertyChanged(nameof(LatestTaskArtifact));
        OnPropertyChanged(nameof(TaskProgressEvidenceText));
        OnPropertyChanged(nameof(TaskProgressToolText));
        OnPropertyChanged(nameof(TaskProgressArtifactText));
        OnPropertyChanged(nameof(TaskProgressNextAction));
        ExportLatestTaskArtifactCommand.NotifyCanExecuteChanged();
    }

    private IEnumerable<SourceRef> AllTaskSources() => Messages
        .SelectMany(message => message.Sources ?? []);

    private ChatMessage? LatestTaskOutput => Messages.LastOrDefault(message =>
        string.Equals(message.Role, "assistant", StringComparison.OrdinalIgnoreCase)
        && !message.IsLoading
        && !string.IsNullOrWhiteSpace(message.Content));

    private static void AppendTaskField(System.Text.StringBuilder builder, string label, string value)
    {
        if (!string.IsNullOrWhiteSpace(value))
        {
            builder.AppendLine($"- {label}：{value.Trim()}");
        }
    }

    private static string SourceIdentity(SourceRef source)
    {
        if (source.IsWebSource)
        {
            return $"web:{source.Url ?? source.Source}";
        }
        return $"local:{source.Source}:{source.ChunkId?.ToString() ?? source.Page?.ToString() ?? ""}";
    }
}
