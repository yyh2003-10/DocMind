using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class GraphViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;
    private readonly NotificationService? _notifications;

    private string? _collection = "全部集合";
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private GraphResponse? _graphData;
    private string _graphJson = "{\"nodes\":[],\"edges\":[]}";
    private bool _hasGraph;
    private GraphNode? _selectedNode;
    private bool _isDetailOpen;
    private int _totalNodes;
    private int _totalEdges;
    private bool _hasLoadedOnce;

    public GraphViewModel(IDoc2kbApiService apiService, NotificationService? notifications = null)
    {
        _apiService = apiService;
        _notifications = notifications;
        Title = "知识图谱";
        SelectedNodeRelations = new ObservableCollection<GraphEntityRelation>();
        ContextSnippets = new ObservableCollection<GraphContextSnippet>();
        SourceDocuments = new ObservableCollection<GraphSourceDocument>();
        Collections = new ObservableCollection<string>();
        EntityChatMessages = new ObservableCollection<ChatMessage>();
        AdaptiveQuickPrompts = new ObservableCollection<string>();
        DistilledTags = new ObservableCollection<string>();
    }

    private CancellationTokenSource? _chatCts;
    private string _entityChatInput = string.Empty;
    private bool _isWebSearchEnabled = true;
    private bool _isEntityAiGenerating;
    private string? _currentEntityChatId;
    private bool _isDistillDialogOpen;
    private string _distilledMarkdownCard = string.Empty;
    private bool _isDistilling;

    public ObservableCollection<GraphEntityRelation> SelectedNodeRelations { get; }
    public ObservableCollection<GraphContextSnippet> ContextSnippets { get; }
    public ObservableCollection<GraphSourceDocument> SourceDocuments { get; }
    public ObservableCollection<string> Collections { get; }
    public ObservableCollection<ChatMessage> EntityChatMessages { get; }
    public ObservableCollection<string> AdaptiveQuickPrompts { get; }
    public ObservableCollection<string> DistilledTags { get; }

    public bool HasRelations => SelectedNodeRelations.Count > 0;
    public bool HasSnippets => ContextSnippets.Count > 0;
    public bool HasSourceDocuments => SourceDocuments.Count > 0;
    public bool HasEntityChatMessages => EntityChatMessages.Count > 0;

    public string EntityChatInput
    {
        get => _entityChatInput;
        set => SetProperty(ref _entityChatInput, value);
    }

    public bool IsWebSearchEnabled
    {
        get => _isWebSearchEnabled;
        set => SetProperty(ref _isWebSearchEnabled, value);
    }

    public bool IsEntityAiGenerating
    {
        get => _isEntityAiGenerating;
        set
        {
            if (SetProperty(ref _isEntityAiGenerating, value))
            {
                SendEntityChatCommand.NotifyCanExecuteChanged();
                StopEntityChatCommand.NotifyCanExecuteChanged();
            }
        }
    }

    public bool IsDistillDialogOpen
    {
        get => _isDistillDialogOpen;
        set => SetProperty(ref _isDistillDialogOpen, value);
    }

    public string DistilledMarkdownCard
    {
        get => _distilledMarkdownCard;
        set => SetProperty(ref _distilledMarkdownCard, value);
    }

    public bool IsDistilling
    {
        get => _isDistilling;
        set => SetProperty(ref _isDistilling, value);
    }

    public event Action<string>? GraphDataRenderRequested;
    public event Action<string>? ThemeChangeRequested;
    public event Action<string>? NodeFocusRequested;

    public string? Collection
    {
        get => _collection;
        set
        {
            if (SetProperty(ref _collection, value))
            {
                _ = LoadGraphAsync();
            }
        }
    }

    public bool ShowEmptyGraph => !IsBusy && TotalNodes == 0;

    public bool IsBusy
    {
        get => _isBusy;
        set
        {
            if (SetProperty(ref _isBusy, value))
            {
                LoadGraphCommand.NotifyCanExecuteChanged();
                ExtractGraphCommand.NotifyCanExecuteChanged();
                OnPropertyChanged(nameof(ShowEmptyGraph));
            }
        }
    }

    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    public GraphResponse? GraphData
    {
        get => _graphData;
        private set => SetProperty(ref _graphData, value);
    }

    public string GraphJson
    {
        get => _graphJson;
        private set => SetProperty(ref _graphJson, value);
    }

    public bool HasGraph
    {
        get => _hasGraph;
        private set => SetProperty(ref _hasGraph, value);
    }

    public GraphNode? SelectedNode
    {
        get => _selectedNode;
        set => SetProperty(ref _selectedNode, value);
    }

    public bool IsDetailOpen
    {
        get => _isDetailOpen;
        set => SetProperty(ref _isDetailOpen, value);
    }

    public int TotalNodes
    {
        get => _totalNodes;
        private set
        {
            if (SetProperty(ref _totalNodes, value))
            {
                OnPropertyChanged(nameof(ShowEmptyGraph));
            }
        }
    }

    public int TotalEdges
    {
        get => _totalEdges;
        private set => SetProperty(ref _totalEdges, value);
    }

    public async Task EnsureLoadedAsync()
    {
        if (_hasLoadedOnce)
        {
            return;
        }
        _hasLoadedOnce = true;
        await LoadCollectionsAsync();
        await LoadGraphAsync();
    }

    /// <summary>使知识图谱缓存失效，下次进入或导入新文档后自动重载图谱。</summary>
    public void InvalidateCache() => _hasLoadedOnce = false;

    public void NotifyThemeChanged(string theme)
    {
        ThemeChangeRequested?.Invoke(theme);
    }

    [RelayCommand]
    public async Task LoadCollectionsAsync()
    {
        try
        {
            var stats = await _apiService.GetStatsAsync();
            Collections.Clear();
            Collections.Add("全部集合");
            if (stats?.Collections != null)
            {
                foreach (var collName in stats.Collections.Keys)
                {
                    Collections.Add(collName);
                }
            }
            if (string.IsNullOrWhiteSpace(_collection) || !Collections.Contains(_collection))
            {
                _collection = "全部集合";
                OnPropertyChanged(nameof(Collection));
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"加载集合列表失败: {ex.Message}", "GraphVM");
        }
    }

    [RelayCommand(CanExecute = nameof(CanOperate))]
    public async Task LoadGraphAsync()
    {
        IsBusy = true;
        StatusMessage = "正在加载知识图谱...";
        try
        {
            string? targetColl = (_collection == "全部集合" || string.IsNullOrWhiteSpace(_collection)) ? null : _collection;
            var resp = await _apiService.GetGraphAsync(targetColl);
            GraphData = resp;
            TotalNodes = resp.TotalNodes;
            TotalEdges = resp.Edges?.Count ?? 0;
            HasGraph = TotalNodes > 0;

            GraphJson = JsonSerializer.Serialize(resp);
            GraphDataRenderRequested?.Invoke(GraphJson);

            StatusMessage = HasGraph ? "就绪" : "当前集合暂无图谱数据（可点击「抽取图谱」构建）";
        }
        catch (Exception ex)
        {
            StatusMessage = $"加载图谱失败: {ex.Message}";
            DebugLog.Error($"加载图谱失败: {ex}", "GraphVM");
        }
        finally
        {
            IsBusy = false;
        }
    }

    [RelayCommand(CanExecute = nameof(CanOperate))]
    public async Task ExtractGraphAsync()
    {
        IsBusy = true;
        StatusMessage = "大模型正在从文档中抽取实体与关系网，请稍候...";
        try
        {
            string? targetColl = (_collection == "全部集合" || string.IsNullOrWhiteSpace(_collection)) ? null : _collection;
            var result = await _apiService.ExtractGraphAsync(targetColl, topK: 30);
            if (result.Ok)
            {
                if (result.ExtractedCount > 0)
                {
                    _notifications?.Success($"成功从 {result.ExtractedCount} 篇文档中抽取实体并构建图谱！", "图谱生成成功");
                }
                else
                {
                    _notifications?.Info("未发现需要抽取的新文档，或已有图谱已是最新状态。", "提示");
                }
                await LoadGraphAsync();
            }
            else
            {
                var errMsg = result.Errors != null && result.Errors.Count > 0 ? string.Join("; ", result.Errors) : "抽取失败";
                StatusMessage = $"抽取失败: {errMsg}";
                _notifications?.Warning(errMsg, "抽取失败");
            }
        }
        catch (ApiException ex)
        {
            StatusMessage = $"提示: {ex.Message}";
            _notifications?.Warning(ex.Message, "抽取提示");
            DebugLog.Warn($"图谱抽取提示: {ex.Message}", "GraphVM");
        }
        catch (BackendConnectionException ex)
        {
            StatusMessage = "无法连接到后端服务，请确认后端进程已正常运行。";
            _notifications?.Error(StatusMessage, "连接失败");
            DebugLog.Error($"图谱抽取连接失败: {ex}", "GraphVM");
        }
        catch (Exception ex)
        {
            StatusMessage = $"图谱抽取异常: {ex.Message}";
            _notifications?.Error($"图谱抽取异常: {ex.Message}", "错误");
            DebugLog.Error($"图谱抽取异常: {ex}", "GraphVM");
        }
        finally
        {
            IsBusy = false;
        }
    }

    private bool CanOperate() => !IsBusy;

    public async Task SelectNodeAsync(string nodeId)
    {
        if (GraphData == null || string.IsNullOrWhiteSpace(nodeId))
        {
            return;
        }

        var node = GraphData.Nodes.FirstOrDefault(n => n.Id == nodeId);
        if (node == null)
        {
            return;
        }

        // 切换不同节点时重置对话会话
        if (SelectedNode?.Id != node.Id)
        {
            _chatCts?.Cancel();
            EntityChatMessages.Clear();
            _currentEntityChatId = null;
            EntityChatInput = string.Empty;
            IsDistillDialogOpen = false;
        }

        SelectedNode = node;
        IsDetailOpen = true;
        PopulateAdaptivePrompts(node);

        try
        {
            var detail = await _apiService.GetEntityDetailAsync(nodeId);
            
            SelectedNodeRelations.Clear();
            if (detail?.Relations != null)
            {
                foreach (var r in detail.Relations)
                {
                    SelectedNodeRelations.Add(r);
                }
            }

            ContextSnippets.Clear();
            if (detail?.Snippets != null)
            {
                foreach (var s in detail.Snippets)
                {
                    ContextSnippets.Add(s);
                }
            }

            SourceDocuments.Clear();
            if (detail?.SourceDocuments != null)
            {
                foreach (var d in detail.SourceDocuments)
                {
                    SourceDocuments.Add(d);
                }
            }

            OnPropertyChanged(nameof(HasRelations));
            OnPropertyChanged(nameof(HasSnippets));
            OnPropertyChanged(nameof(HasSourceDocuments));
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"获取实体详情失败: {ex.Message}", "GraphVM");
        }
    }

    private void PopulateAdaptivePrompts(GraphNode node)
    {
        AdaptiveQuickPrompts.Clear();
        var type = node.Type.ToLowerInvariant();
        if (type is "tech" or "code" or "api" or "class")
        {
            AdaptiveQuickPrompts.Add($"💡 核心机制与设计原理");
            AdaptiveQuickPrompts.Add($"🛠️ 最佳实践与重构代码");
            AdaptiveQuickPrompts.Add($"⚠️ 常见排错与踩坑指南");
            AdaptiveQuickPrompts.Add($"🌐 联网检索业界最新方案与演进");
        }
        else if (type is "concept" or "arch" or "pattern")
        {
            AdaptiveQuickPrompts.Add($"💡 通俗解释与应用场景");
            AdaptiveQuickPrompts.Add($"⚖️ 优缺点与技术选型对比");
            AdaptiveQuickPrompts.Add($"🔄 架构演进与上下游关系");
            AdaptiveQuickPrompts.Add($"🌐 联网检索前沿行业规范");
        }
        else
        {
            AdaptiveQuickPrompts.Add($"💡 详细解读核心背景");
            AdaptiveQuickPrompts.Add($"🕸️ 与其他模块的协作模式");
            AdaptiveQuickPrompts.Add($"🌐 联网检索相关最新动态");
        }
    }

    [RelayCommand]
    public async Task SendEntityChatAsync(string? promptOverride = null)
    {
        if (SelectedNode == null || IsEntityAiGenerating)
        {
            return;
        }

        var query = !string.IsNullOrWhiteSpace(promptOverride) ? promptOverride.Trim() : EntityChatInput.Trim();
        if (string.IsNullOrWhiteSpace(query))
        {
            return;
        }

        EntityChatInput = string.Empty;

        // 添加用户消息
        var userMsg = new ChatMessage
        {
            Role = "user",
            Content = query
        };
        EntityChatMessages.Add(userMsg);

        // 创建助手占位消息
        var assistantMsg = new ChatMessage
        {
            Role = "assistant",
            Content = "",
            IsLoading = true
        };
        EntityChatMessages.Add(assistantMsg);

        IsEntityAiGenerating = true;
        _chatCts = new CancellationTokenSource();
        var ct = _chatCts.Token;

        // 组装 High-level 实体图谱拓扑与背景
        var relationsSummary = string.Join(", ", SelectedNodeRelations.Select(r => $"{r.Relation} -> {r.ToName} ({r.ToType})"));
        var entityContext = $"实体: {SelectedNode.Name}\n分类: {SelectedNode.Type}\n所属知识库: {SelectedNode.Collection}\n关联关系网: {(string.IsNullOrWhiteSpace(relationsSummary) ? "无" : relationsSummary)}";

        var chatReq = new ChatRequest
        {
            Query = query,
            Collection = SelectedNode.Collection,
            ChatId = _currentEntityChatId,
            EnableWebSearch = IsWebSearchEnabled,
            EntityContext = entityContext
        };

        try
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            var streamResult = await _apiService.ChatStreamAsync(
                chatReq,
                onToken: token =>
                {
                    System.Windows.Application.Current?.Dispatcher?.Invoke(() =>
                    {
                        assistantMsg.IsLoading = false;
                        assistantMsg.AppendToken(token);
                    });
                },
                onDone: doneResult =>
                {
                    System.Windows.Application.Current?.Dispatcher?.Invoke(() =>
                    {
                        assistantMsg.IsLoading = false;
                        assistantMsg.Model = doneResult.Model;
                        assistantMsg.Provider = doneResult.Provider;
                        assistantMsg.ElapsedMs = doneResult.ElapsedMs;
                        assistantMsg.Sources = doneResult.Sources;
                        assistantMsg.ForceRefreshRender();
                        if (!string.IsNullOrWhiteSpace(doneResult.ChatId))
                        {
                            _currentEntityChatId = doneResult.ChatId;
                        }
                    });
                },
                ct
            );

            if (assistantMsg.Sources == null || assistantMsg.Sources.Count == 0)
            {
                assistantMsg.Sources = streamResult.Sources;
            }
        }
        catch (OperationCanceledException)
        {
            assistantMsg.IsLoading = false;
            assistantMsg.Content += "\n\n*(已手动停止生成)*";
            assistantMsg.ForceRefreshRender();
        }
        catch (Exception ex)
        {
            assistantMsg.IsLoading = false;
            assistantMsg.Content = $"⚠️ 抱歉，AI 问答出现异常: {ex.Message}";
            assistantMsg.ForceRefreshRender();
            DebugLog.Error($"实体 AI 问答失败: {ex}", "GraphVM");
        }
        finally
        {
            IsEntityAiGenerating = false;
            OnPropertyChanged(nameof(HasEntityChatMessages));
        }
    }

    [RelayCommand]
    public void StopEntityChat()
    {
        _chatCts?.Cancel();
    }

    [RelayCommand]
    public void ClearEntityChat()
    {
        _chatCts?.Cancel();
        EntityChatMessages.Clear();
        _currentEntityChatId = null;
        OnPropertyChanged(nameof(HasEntityChatMessages));
    }

    [RelayCommand]
    public async Task DistillEntityCardAsync()
    {
        if (SelectedNode == null || IsDistilling)
        {
            return;
        }

        IsDistilling = true;
        try
        {
            var dialogueBuilder = new System.Text.StringBuilder();
            foreach (var msg in EntityChatMessages)
            {
                dialogueBuilder.AppendLine($"【{msg.Role}】: {msg.Content}\n");
            }

            var snippets = ContextSnippets.Select(s => s.Content).ToList();
            var webRefs = EntityChatMessages
                .Where(m => m.Sources != null)
                .SelectMany(m => m.Sources!)
                .Where(s => s.IsWebSource)
                .Select(s => $"{s.Title} ({s.Url})")
                .Distinct()
                .ToList();

            var req = new EntityDistillRequest
            {
                EntityId = SelectedNode.Id,
                EntityName = SelectedNode.Name,
                EntityType = SelectedNode.Type,
                Collection = SelectedNode.Collection,
                DialogueSummary = dialogueBuilder.ToString(),
                LocalSnippets = snippets,
                WebReferences = webRefs
            };

            var res = await _apiService.DistillEntityKnowledgeAsync(req);
            DistilledMarkdownCard = res.MarkdownCard;
            DistilledTags.Clear();
            foreach (var t in res.SuggestedTags)
            {
                DistilledTags.Add(t);
            }

            IsDistillDialogOpen = true;
            _notifications?.Success($"已成功提炼实体「{SelectedNode.Name}」知识精炼卡片", "知识蒸馏");
        }
        catch (Exception ex)
        {
            _notifications?.Error($"知识卡片蒸馏失败: {ex.Message}", "错误");
            DebugLog.Error($"知识卡片蒸馏异常: {ex}", "GraphVM");
        }
        finally
        {
            IsDistilling = false;
        }
    }

    [RelayCommand]
    public async Task IngestDistilledCardAsync()
    {
        if (SelectedNode == null || string.IsNullOrWhiteSpace(DistilledMarkdownCard))
        {
            return;
        }

        try
        {
            var tagsText = DistilledTags.Count > 0 ? $"标签：{string.Join(" ", DistilledTags.Select(t => $"#{t}"))}\n\n" : "";
            var fullNote = $"{tagsText}{DistilledMarkdownCard}";

            var req = new IngestTextRequest
            {
                Text = fullNote,
                Title = $"【知识档案】{SelectedNode.Name}",
                Collection = SelectedNode.Collection ?? "default",
                Force = true
            };

            await _apiService.IngestTextAsync(req);
            IsDistillDialogOpen = false;
            _notifications?.Success($"已将「{SelectedNode.Name}」精炼知识卡片沉淀入库！", "沉淀成功");
        }
        catch (Exception ex)
        {
            _notifications?.Error($"沉淀入库失败: {ex.Message}", "错误");
            DebugLog.Error($"沉淀入库异常: {ex}", "GraphVM");
        }
    }

    [RelayCommand]
    public void CloseDistillDialog()
    {
        IsDistillDialogOpen = false;
    }

    [RelayCommand]
    public async Task NavigateToEntityAsync(object? param)
    {
        string? targetId = null;
        if (param is GraphEntityRelation rel)
        {
            targetId = (rel.ToId != SelectedNode?.Id && !string.IsNullOrEmpty(rel.ToId)) ? rel.ToId : rel.FromId;
        }
        else if (param is string id)
        {
            targetId = id;
        }

        if (!string.IsNullOrWhiteSpace(targetId))
        {
            await SelectNodeAsync(targetId);
            NodeFocusRequested?.Invoke(targetId);
        }
    }

    [RelayCommand]
    public void CopyEntityName()
    {
        if (SelectedNode != null && !string.IsNullOrWhiteSpace(SelectedNode.Name))
        {
            try
            {
                System.Windows.Clipboard.SetText(SelectedNode.Name);
                _notifications?.Success($"已复制实体「{SelectedNode.Name}」", "复制成功");
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"复制实体名称失败: {ex.Message}", "GraphVM");
            }
        }
    }

    [RelayCommand]
    public void CopySnippetContent(object? param)
    {
        string? text = (param as GraphContextSnippet)?.Content ?? param as string;
        if (!string.IsNullOrWhiteSpace(text))
        {
            try
            {
                System.Windows.Clipboard.SetText(text);
                _notifications?.Success("已复制知识片段内容", "复制成功");
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"复制知识片段失败: {ex.Message}", "GraphVM");
            }
        }
    }

    [RelayCommand]
    public void OpenSourceDocument(object? param)
    {
        string? filePath = (param as GraphSourceDocument)?.Source 
            ?? (param as GraphContextSnippet)?.Source 
            ?? param as string;

        if (string.IsNullOrWhiteSpace(filePath))
        {
            return;
        }

        try
        {
            if (System.IO.File.Exists(filePath) || System.IO.Directory.Exists(filePath))
            {
                var psi = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = filePath,
                    UseShellExecute = true
                };
                System.Diagnostics.Process.Start(psi);
                _notifications?.Info($"正在打开: {System.IO.Path.GetFileName(filePath)}", "打开文档");
            }
            else
            {
                System.Windows.Clipboard.SetText(filePath);
                _notifications?.Info($"文档文件不存在，已复制路径: {filePath}", "提示");
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"打开文档失败: {ex.Message}", "GraphVM");
            try
            {
                System.Windows.Clipboard.SetText(filePath);
                _notifications?.Info($"已复制文档路径: {filePath}", "提示");
            }
            catch { }
        }
    }

    [RelayCommand]
    public void CloseDetail()
    {
        _chatCts?.Cancel();
        IsDetailOpen = false;
        SelectedNode = null;
        SelectedNodeRelations.Clear();
        ContextSnippets.Clear();
        SourceDocuments.Clear();
        EntityChatMessages.Clear();
        _currentEntityChatId = null;
        IsDistillDialogOpen = false;
        OnPropertyChanged(nameof(HasRelations));
        OnPropertyChanged(nameof(HasSnippets));
        OnPropertyChanged(nameof(HasSourceDocuments));
        OnPropertyChanged(nameof(HasEntityChatMessages));
    }

    [RelayCommand]
    public void ToggleDetail()
    {
        IsDetailOpen = !IsDetailOpen;
    }
}
