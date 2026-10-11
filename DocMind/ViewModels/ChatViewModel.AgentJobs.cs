using System.Text.Json;
using System.Windows;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

/// <summary>Durable background-Agent execution and crash/restart reattachment.</summary>
public partial class ChatViewModel
{
    private string? _activeAgentJobId;
    private CancellationTokenSource? _agentJobWatchCts;
    private ChatMessage? _agentJobMessage;
    private bool _isAgentJobRunning;
    private int _agentJobProgressPercent;
    private string _agentJobStage = string.Empty;
    private string _agentJobStatus = string.Empty;

    public bool IsAgentJobModeAvailable => SelectedChatMode.Key == "agent" && _appSettings.AgentModeEnabled;

    public bool IsAgentJobRunning
    {
        get => _isAgentJobRunning;
        private set
        {
            if (SetProperty(ref _isAgentJobRunning, value))
            {
                OnPropertyChanged(nameof(ShowAgentJobProgress));
                OnPropertyChanged(nameof(AgentJobProgressText));
                SendCommand.NotifyCanExecuteChanged();
                StopCommand.NotifyCanExecuteChanged();
                StartAgentJobCommand.NotifyCanExecuteChanged();
                ShowStopChanged();
            }
        }
    }

    public bool ShowAgentJobProgress => IsAgentJobRunning || !string.IsNullOrWhiteSpace(_agentJobStatus);

    public int AgentJobProgressPercent
    {
        get => _agentJobProgressPercent;
        private set
        {
            if (SetProperty(ref _agentJobProgressPercent, Math.Clamp(value, 0, 100)))
            {
                OnPropertyChanged(nameof(AgentJobProgressText));
            }
        }
    }

    public string AgentJobStage
    {
        get => _agentJobStage;
        private set
        {
            if (SetProperty(ref _agentJobStage, value))
            {
                OnPropertyChanged(nameof(AgentJobProgressText));
            }
        }
    }

    public string AgentJobStatus
    {
        get => _agentJobStatus;
        private set
        {
            if (SetProperty(ref _agentJobStatus, value))
            {
                OnPropertyChanged(nameof(ShowAgentJobProgress));
                OnPropertyChanged(nameof(AgentJobProgressText));
            }
        }
    }

    public string AgentJobProgressText
    {
        get
        {
            var stage = string.IsNullOrWhiteSpace(AgentJobStage) ? "处理中" : AgentJobStage;
            return IsAgentJobRunning ? $"后台 Agent：{stage} · {AgentJobProgressPercent}%" : AgentJobStatus;
        }
    }

    private bool CanStartAgentJob()
        => IsAgentJobModeAvailable && !IsBusy && !IsAgentJobRunning && HasInput && IsLlmConfigured;

    [RelayCommand(CanExecute = nameof(CanStartAgentJob))]
    private async Task StartAgentJobAsync()
    {
        if (!CanStartAgentJob())
        {
            return;
        }

        var query = InputText.Trim();
        var attachments = PendingAttachments.Select(item => item.FullPath).ToList();
        var attachmentLabels = PendingAttachments.Select(item => $"{item.Icon} {item.FileName}").ToList();
        PendingAttachments.Clear();
        InputText = string.Empty;
        OnPropertyChanged(nameof(HasPendingAttachments));
        OnPropertyChanged(nameof(HasInput));
        SendCommand.NotifyCanExecuteChanged();

        var userContent = attachmentLabels.Count == 0
            ? query
            : $"[附件: {string.Join(", ", attachmentLabels)}]\n{query}";
        var userMessage = new ChatMessage { Role = "user", Content = userContent };
        AttachFeedbackSink(userMessage);
        Messages.Add(userMessage);

        var assistant = new ChatMessage
        {
            Role = "assistant",
            Content = string.Empty,
            IsLoading = true,
            IsWaitingForFirstToken = true,
            AnswerFormat = IsHtmlAnswerMode ? "html" : "markdown",
        };
        AttachFeedbackSink(assistant);
        Messages.Add(assistant);
        _agentJobMessage = assistant;

        try
        {
            var request = await BuildAgentJobRequestAsync(query, attachments).ConfigureAwait(true);
            StatusMessage = "正在提交后台 Agent 任务…";
            var job = await _apiService.StartAgentJobAsync(request).ConfigureAwait(true);
            _activeAgentJobId = job.JobId;
            _chatId = string.IsNullOrWhiteSpace(request.ChatId) ? _chatId : request.ChatId;
            IsAgentJobRunning = true;
            ApplyAgentJobProgress(job);
            await SaveAgentCheckpointAsync(job.JobId, request).ConfigureAwait(true);
            _ = WatchAgentJobAsync(job.JobId, assistant);
        }
        catch (Exception ex)
        {
            assistant.IsLoading = false;
            assistant.IsWaitingForFirstToken = false;
            assistant.Content = $"后台 Agent 任务启动失败：{ex.Message}";
            StatusMessage = "后台 Agent 任务未启动";
            _notifications?.Error("后台 Agent 任务启动失败");
            DebugLog.Warn($"后台 Agent 任务启动失败: {ex.Message}", "Chat");
        }
    }

    private async Task<ChatRequest> BuildAgentJobRequestAsync(string query, IReadOnlyList<string> attachments)
    {
        var memoryContext = string.Empty;
        if (_userMemory is { } memory && _appSettings.MemoryEnabled)
        {
            try
            {
                memoryContext = await memory.BuildSystemPromptInjectionAsync(query).ConfigureAwait(true);
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"后台 Agent 记忆注入失败: {ex.Message}", "Memory");
            }
        }

        var choice = SelectedModelChoice;
        var isDefaultChoice = choice is null || choice.IsDefault;
        var provider = choice?.Provider;
        var model = isDefaultChoice ? null : (choice?.Model ?? SelectedModel);
        return new ChatRequest
        {
            Query = query,
            Collections = SelectedCollections.Count > 0 ? SelectedCollections : null,
            ChatId = _chatId,
            Model = model,
            ProviderConfig = provider is null || isDefaultChoice
                ? null
                : new ProviderConfig
                {
                    Provider = provider.Provider,
                    ApiKey = provider.ApiKey,
                    BaseUrl = provider.BaseUrl,
                    Model = model,
                },
            Persona = SelectedPersona?.Id,
            PersonaPrompt = SelectedPersona is { IsCustom: true }
                ? (string.IsNullOrWhiteSpace(SelectedPersona.Description) ? SelectedPersona.DisplayName : SelectedPersona.Description)
                : null,
            EnableWebSearch = IsWebSearchEnabled,
            WebSearchMode = IsWebSearchEnabled ? SelectedWebSearchMode.Key : null,
            Attachments = attachments.Count > 0 ? attachments : null,
            GithubToken = string.IsNullOrWhiteSpace(_appSettings.GithubToken) ? null : _appSettings.GithubToken.Trim(),
            RagMode = _appSettings.RagMode,
            MemoryContext = string.IsNullOrWhiteSpace(memoryContext) ? null : memoryContext,
            TaskContext = BuildTaskContext(),
            ResponseMode = SelectedPersona?.Id is "ppt" or "doc" or "lesson" or "table" or "web" ? "delivery" : null,
            ChatMode = "agent",
            AgentMode = true,
            AnswerFormat = IsHtmlAnswerMode ? "html" : null,
        };
    }

    private async Task WatchAgentJobAsync(string jobId, ChatMessage message)
    {
        _agentJobWatchCts?.Cancel();
        _agentJobWatchCts?.Dispose();
        _agentJobWatchCts = new CancellationTokenSource();
        try
        {
            var progress = new Progress<JobStatus>(ApplyAgentJobProgress);
            var final = await _apiService.WatchJobUntilDoneAsync(jobId, progress, _agentJobWatchCts.Token).ConfigureAwait(true);
            await ApplyAgentJobTerminalAsync(final, message).ConfigureAwait(true);
        }
        catch (OperationCanceledException)
        {
            // Navigation/replacement only interrupts the local watcher. The server job remains resumable.
        }
        catch (Exception ex)
        {
            IsAgentJobRunning = false;
            AgentJobStatus = $"后台 Agent 连接中断；可恢复任务 {jobId}";
            message.IsLoading = false;
            message.IsWaitingForFirstToken = false;
            message.TrajectoryWarning = "后台任务仍可能在服务端运行，重启或返回对话后会自动重连。";
            StatusMessage = AgentJobStatus;
            DebugLog.Warn($"后台 Agent 任务观察中断 {jobId}: {ex.Message}", "Chat");
        }
    }

    private void ApplyAgentJobProgress(JobStatus job)
    {
        AgentJobProgressPercent = (int)Math.Round(job.Progress * 100);
        AgentJobStage = job.Stage ?? "处理中";
        AgentJobStatus = job.Status ?? "running";
        if (!string.IsNullOrWhiteSpace(job.PermissionRequestId))
        {
            PendingPermissionRequestId = job.PermissionRequestId;
            PendingPermissionToolId = job.PermissionToolId ?? string.Empty;
            HasPendingPermission = true;
        }
        else if (HasPendingPermission)
        {
            PendingPermissionRequestId = null;
            PendingPermissionToolId = string.Empty;
            HasPendingPermission = false;
        }
    }

    private async Task ApplyAgentJobTerminalAsync(JobStatus job, ChatMessage message)
    {
        ApplyAgentJobProgress(job);
        IsAgentJobRunning = false;
        _activeAgentJobId = null;
        _agentJobWatchCts?.Dispose();
        _agentJobWatchCts = null;
        await ClearAgentCheckpointAsync(job.JobId).ConfigureAwait(true);

        message.IsLoading = false;
        message.IsWaitingForFirstToken = false;
        if (string.Equals(job.Status, "completed", StringComparison.OrdinalIgnoreCase) && job.Result is { } result)
        {
            message.Model = result.Model;
            message.Provider = result.Provider;
            message.ElapsedMs = result.ElapsedMs;
            message.Sources = result.Sources;
            message.TrajectorySteps = result.Trajectory;
            message.TrajectoryWarning = result.TrajectoryWarning;
            message.Partial = result.Partial;
            message.TruncatedHint = result.Warning;
            SetAgentJobMessageContent(message, result.Answer);
            message.CompleteThinking(result.ElapsedMs);
            if (!string.IsNullOrWhiteSpace(result.ChatId))
            {
                _chatId = result.ChatId;
            }
            StatusMessage = result.Partial && !string.IsNullOrWhiteSpace(result.Warning)
                ? $"后台 Agent 已结束：{result.Warning}"
                : "后台 Agent 已完成";
            _notifications?.Success("后台 Agent 已完成");
        }
        else
        {
            var cancelled = string.Equals(job.Status, "cancelled", StringComparison.OrdinalIgnoreCase);
            message.Partial = true;
            message.TrajectoryWarning = cancelled ? "任务已由用户取消。" : job.Error;
            SetAgentJobMessageContent(message, cancelled ? "后台 Agent 已取消。" : $"后台 Agent 失败：{job.Error ?? "未知错误"}");
            StatusMessage = cancelled ? "后台 Agent 已取消" : "后台 Agent 失败";
            if (cancelled) _notifications?.Warning("后台 Agent 已取消");
            else _notifications?.Error("后台 Agent 执行失败");
        }
        RefreshTaskProgress();
    }

    private static void SetAgentJobMessageContent(ChatMessage message, string content)
    {
        try
        {
            message.Content = content;
        }
        catch (Exception ex)
        {
            // A presentation-layer rendering error must not discard a durable job's
            // sources, trajectory, model metadata, or terminal state.
            DebugLog.Warn($"后台 Agent 结果渲染失败: {ex.Message}", "Chat");
        }
    }

    private async Task CancelAgentJobAsync()
    {
        var jobId = _activeAgentJobId;
        if (string.IsNullOrWhiteSpace(jobId))
        {
            return;
        }
        try
        {
            await _apiService.CancelJobAsync(jobId).ConfigureAwait(true);
            StatusMessage = "已请求取消后台 Agent 任务…";
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"取消后台 Agent 任务失败: {ex.Message}", "Chat");
            _notifications?.Warning("未能向服务端发送取消请求");
        }
    }

    private async Task SaveAgentCheckpointAsync(string jobId, ChatRequest request)
    {
        if (_checkpoint is null) return;
        try
        {
            await _checkpoint.SaveCheckpointAsync(new CheckpointState
            {
                OperationId = jobId,
                OperationType = "agent",
                CompletedItems = 0,
                TotalItems = 1,
                Metadata = new Dictionary<string, string>
                {
                    ["chatId"] = request.ChatId ?? string.Empty,
                    ["query"] = request.Query,
                },
            }).ConfigureAwait(true);
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"保存 Agent 任务检查点失败: {ex.Message}", "Chat");
        }
    }

    private async Task ClearAgentCheckpointAsync(string jobId)
    {
        if (_checkpoint is null) return;
        try { await _checkpoint.ClearCheckpointAsync(jobId).ConfigureAwait(true); }
        catch (Exception ex) { DebugLog.Warn($"清理 Agent 检查点失败: {ex.Message}", "Chat"); }
    }

    public async Task ResumeAgentJobAsync()
    {
        if (_checkpoint is null || IsAgentJobRunning) return;
        try
        {
            var pending = await _checkpoint.ListPendingCheckpointsAsync().ConfigureAwait(true);
            // The constructor starts this recovery asynchronously. A newly submitted
            // Agent job may save its checkpoint before the disk read completes; do
            // not attach a second watcher to that live job.
            if (IsAgentJobRunning || !string.IsNullOrWhiteSpace(_activeAgentJobId)) return;
            var checkpoint = pending
                .Where(item => string.Equals(item.OperationType, "agent", StringComparison.OrdinalIgnoreCase))
                .OrderByDescending(item => item.SavedAt)
                .FirstOrDefault();
            if (checkpoint is null || string.IsNullOrWhiteSpace(checkpoint.OperationId)) return;

            _activeAgentJobId = checkpoint.OperationId;
            _chatId = checkpoint.Metadata.TryGetValue("chatId", out var savedChatId) && !string.IsNullOrWhiteSpace(savedChatId)
                ? savedChatId
                : _chatId;
            var message = Messages.LastOrDefault(item => item.Role == "assistant" && item.IsLoading);
            if (message is null)
            {
                message = new ChatMessage { Role = "assistant", Content = string.Empty, IsLoading = true, IsWaitingForFirstToken = true };
                AttachFeedbackSink(message);
                Messages.Add(message);
            }
            _agentJobMessage = message;
            IsAgentJobRunning = true;
            AgentJobStatus = "正在恢复后台 Agent 任务…";
            _ = WatchAgentJobAsync(checkpoint.OperationId, message);
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"恢复后台 Agent 任务失败: {ex.Message}", "Chat");
        }
    }
}
