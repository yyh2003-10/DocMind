using System.Collections.ObjectModel;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class GraphViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;
    private readonly NotificationService? _notifications;
    private readonly AppSettings? _appSettings;

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
    private bool _loadSucceeded;
    private bool _hasLoadError;

    public GraphViewModel(IDoc2kbApiService apiService, NotificationService? notifications = null, AppSettings? appSettings = null)
    {
        _apiService = apiService;
        _notifications = notifications;
        _appSettings = appSettings;
        Title = "知识图谱";
        SelectedNodeRelations = new ObservableCollection<GraphEntityRelation>();
        ContextSnippets = new ObservableCollection<GraphContextSnippet>();
        SourceDocuments = new ObservableCollection<GraphSourceDocument>();
        Collections = new ObservableCollection<string>();
        EntityChatMessages = new ObservableCollection<ChatMessage>();
        AdaptiveQuickPrompts = new ObservableCollection<string>();
        DistilledTags = new ObservableCollection<string>();
        EntityQuickJumpNames = new ObservableCollection<string>();

        // 图谱实体问答历史上默认开启联网；用户改过模式后沿用持久化值
        if (_appSettings is not null && !string.IsNullOrWhiteSpace(_appSettings.WebSearchMode))
        {
            _selectedWebSearchMode = WebSearchModeChoice.FromKey(_appSettings.WebSearchMode);
        }
        else if (_appSettings?.EnableWebSearch == true)
        {
            _selectedWebSearchMode = WebSearchModeChoice.Normal;
        }
        else
        {
            _selectedWebSearchMode = WebSearchModeChoice.Normal;
        }
    }

    private CancellationTokenSource? _chatCts;
    private string _entityChatInput = string.Empty;
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

    /// <summary>图谱页顶部「实体快速跳转」下拉的实体名列表（取自 /v1/graph/entities）。</summary>
    public ObservableCollection<string> EntityQuickJumpNames { get; }

    private string? _selectedEntityJumpName;

    /// <summary>顶部实体跳转选中项：命中时聚焦图谱节点并打开实体档案。</summary>
    public string? SelectedEntityJumpName
    {
        get => _selectedEntityJumpName;
        set
        {
            if (SetProperty(ref _selectedEntityJumpName, value) && !string.IsNullOrWhiteSpace(value))
            {
                JumpToEntity(value);
                _selectedEntityJumpName = null;  // 允许重复跳转同一实体
                OnPropertyChanged(nameof(SelectedEntityJumpName));
            }
        }
    }

    private void JumpToEntity(string name)
    {
        try
        {
            var node = GraphData?.SafeNodes.FirstOrDefault(n =>
                string.Equals(n.Name, name, StringComparison.OrdinalIgnoreCase));
            if (node == null)
                return;
            NodeFocusRequested?.Invoke(node.Id);
            _ = SelectNodeAsync(node.Id);
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"实体跳转失败: {ex.Message}", "GraphVM");
        }
    }

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
        get => SelectedWebSearchMode.Key is "normal" or "deep";
        set
        {
            if (value)
            {
                if (SelectedWebSearchMode.Key == "off")
                {
                    SelectedWebSearchMode = WebSearchModeChoice.Normal;
                }
            }
            else if (SelectedWebSearchMode.Key != "off")
            {
                SelectedWebSearchMode = WebSearchModeChoice.Off;
            }
        }
    }

    public ObservableCollection<WebSearchModeChoice> WebSearchModes { get; } =
        new(WebSearchModeChoice.All);

    private WebSearchModeChoice _selectedWebSearchMode = WebSearchModeChoice.Normal;

    public WebSearchModeChoice SelectedWebSearchMode
    {
        get => _selectedWebSearchMode;
        set
        {
            var next = value ?? WebSearchModeChoice.Off;
            if (SetProperty(ref _selectedWebSearchMode, next))
            {
                OnPropertyChanged(nameof(IsWebSearchEnabled));
                if (_appSettings is not null)
                {
                    _appSettings.SetWebSearchMode(next.Key);
                    try
                    {
                        _appSettings.Save();
                    }
                    catch
                    {
                        // 落盘失败不阻断图谱对话
                    }
                }
            }
        }
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
    public event Action? NavigateToSettingsRequested;

    /// <summary>LLM 是否已配置（抽取图谱事前禁用判据）。复用 GetActiveProviderConfig 逻辑。</summary>
    public bool IsLlmConfigured => GetActiveProviderConfig() is not null;

    [RelayCommand]
    private void NavigateToSettings() => NavigateToSettingsRequested?.Invoke();

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

    public bool ShowEmptyGraph => !IsBusy && !HasLoadError && TotalNodes == 0;

    /// <summary>图谱请求失败时显示错误态，避免把网络/后端故障误导成「暂无数据」。</summary>
    public bool ShowGraphError => !IsBusy && HasLoadError;

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
                OnPropertyChanged(nameof(ShowGraphError));
            }
        }
    }

    /// <summary>通知 LLM 配置状态变更（设置页保存后调用）。</summary>
    public void NotifyLlmConfigChanged() => OnPropertyChanged(nameof(IsLlmConfigured));

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

    public bool HasLoadError
    {
        get => _hasLoadError;
        private set
        {
            if (SetProperty(ref _hasLoadError, value))
            {
                OnPropertyChanged(nameof(ShowEmptyGraph));
                OnPropertyChanged(nameof(ShowGraphError));
            }
        }
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
        _loadSucceeded = false;
        await LoadCollectionsAsync();
        await LoadGraphAsync();
        _hasLoadedOnce = _loadSucceeded;
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
        _loadSucceeded = false;
        HasLoadError = false;
        StatusMessage = "正在加载知识图谱...";
        try
        {
            string? targetColl = (_collection == "全部集合" || string.IsNullOrWhiteSpace(_collection)) ? null : _collection;
            var resp = await _apiService.GetGraphAsync(targetColl);
            GraphData = resp;
            HasLoadError = false;

            // 权威计数：graph/stats 给出全集实体/关系总数（visualize 可能截断到 limit），
            // 失败时优雅回退到可视化返回的计数（AUD-017 接通 /v1/graph/stats）。
            try
            {
                var stats = await _apiService.GetGraphStatsAsync(targetColl);
                if (stats != null)
                {
                    TotalNodes = stats.EntityCount;
                    TotalEdges = stats.RelationCount;
                }
                else
                {
                    TotalNodes = resp.TotalNodes;
                    TotalEdges = resp.Edges?.Count ?? 0;
                }
            }
            catch (Exception statsEx)
            {
                DebugLog.Warn($"获取图谱统计失败，回退可视化计数: {statsEx.Message}", "GraphVM");
                TotalNodes = resp.TotalNodes;
                TotalEdges = resp.Edges?.Count ?? 0;
            }
            HasGraph = TotalNodes > 0;

            GraphJson = JsonSerializer.Serialize(resp);
            GraphDataRenderRequested?.Invoke(GraphJson);

            _loadSucceeded = true;
            StatusMessage = HasGraph ? "就绪" : "当前分组暂无图谱数据（可点击「抽取图谱」构建）";

            // 填充实体快速跳转下拉（失败静默，不影响图谱主流程）
            await LoadEntityQuickJumpAsync(targetColl);
        }
        catch (Exception ex)
        {
            HasLoadError = true;
            HasGraph = false;
            GraphData = null;
            TotalNodes = 0;
            TotalEdges = 0;
            GraphJson = "{\"nodes\":[],\"edges\":[]}";
            GraphDataRenderRequested?.Invoke(GraphJson);
            StatusMessage = $"加载图谱失败: {ex.Message}";
            DebugLog.Error($"加载图谱失败: {ex}", "GraphVM");
        }
        finally
        {
            IsBusy = false;
        }
    }

    /// <summary>加载实体快速跳转候选（GET /v1/graph/entities，前 200 个实体名）。</summary>
    private async Task LoadEntityQuickJumpAsync(string? collection)
    {
        try
        {
            var entities = await _apiService.GetGraphEntitiesAsync(collection, limit: 200);
            EntityQuickJumpNames.Clear();
            foreach (var e in entities)
            {
                if (!string.IsNullOrWhiteSpace(e.Name) && !EntityQuickJumpNames.Contains(e.Name))
                {
                    EntityQuickJumpNames.Add(e.Name);
                }
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"加载实体跳转候选失败: {ex.Message}", "GraphVM");
        }
    }

    [RelayCommand(CanExecute = nameof(CanOperate))]
    public async Task ExtractGraphAsync()
    {
        // 事前拦截：LLM 未配置时直接引导配置，不发注定失败的请求
        if (!IsLlmConfigured)
        {
            StatusMessage = "尚未配置大模型：请到【设置 → 大模型】完成配置后重试";
            _notifications?.Warning("尚未配置大模型，无法抽取图谱：请到【设置 → 大模型】完成配置", "需要配置");
            return;
        }

        IsBusy = true;
        StatusMessage = "大模型正在从文档中抽取实体与关系网，请候...";
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
                else if (result.Errors != null && result.Errors.Count > 0)
                {
                    var errMsg = string.Join("; ", result.Errors);
                    _notifications?.Warning($"抽取提示: {errMsg}", "抽取提示");
                }
                else
                {
                    _notifications?.Info("当前分组文档未发现新实体，或已有图谱已是最新状态。", "提示");
                }

                await LoadCollectionsAsync();
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

    private bool CanOperate() => !IsBusy && IsLlmConfigured;

    public async Task SelectNodeAsync(string nodeId)
    {
        if (GraphData == null || string.IsNullOrWhiteSpace(nodeId))
        {
            return;
        }

        var node = GraphData.Nodes?.FirstOrDefault(n => n.Id == nodeId);
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

            // 实体详情加载完成后，异步生成 AI 驱动的快捷探索问题（保留静态预设作为即时回退）
            _ = GenerateAiPromptsAsync(node);
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
        if (type is "topic")
        {
            AdaptiveQuickPrompts.Add($"🪐 概括该业务主题的核心范畴与架构蓝图");
            AdaptiveQuickPrompts.Add($"🕸️ 该主题包含的核心模块与技术栈全景");
            AdaptiveQuickPrompts.Add($"🔄 该主题与知识库其他主题的上下游协同");
            AdaptiveQuickPrompts.Add($"🌐 联网检索该领域前沿实践与行业规范");
        }
        else if (type is "person")
        {
            AdaptiveQuickPrompts.Add($"👤 人物生平履历与核心技术贡献");
            AdaptiveQuickPrompts.Add($"🏢 关联组织机构、代表作与合作脉络");
            AdaptiveQuickPrompts.Add($"🌐 联网检索该人物最新动态与学术成果");
        }
        else if (type is "org")
        {
            AdaptiveQuickPrompts.Add($"🏢 组织机构使命、架构与主力产品");
            AdaptiveQuickPrompts.Add($"👥 关键团队成员与行业生态位");
            AdaptiveQuickPrompts.Add($"🌐 联网检索该组织近期重大发布");
        }
        else if (type is "tech" or "code" or "api" or "class")
        {
            AdaptiveQuickPrompts.Add($"💡 核心机制与底层设计原理");
            AdaptiveQuickPrompts.Add($"🛠️ 最佳实践与典型工程落地");
            AdaptiveQuickPrompts.Add($"⚠️ 常见排错与踩坑指南");
            AdaptiveQuickPrompts.Add($"🌐 联网检索业界最新演进趋势");
        }
        else if (type is "concept" or "arch" or "pattern")
        {
            AdaptiveQuickPrompts.Add($"💡 通俗直观解读与典型应用场景");
            AdaptiveQuickPrompts.Add($"⚖️ 核心优缺点与方案选型权衡");
            AdaptiveQuickPrompts.Add($"🔄 架构演进与上下游依赖体系");
            AdaptiveQuickPrompts.Add($"🌐 联网检索前沿行业权威标准");
        }
        else
        {
            AdaptiveQuickPrompts.Add($"💡 深入解读核心定义与业务背景");
            AdaptiveQuickPrompts.Add($"🕸️ 与其他关键节点的协同与拓扑关系");
            AdaptiveQuickPrompts.Add($"🌐 联网检索相关最新行业动态");
        }
    }

    private static readonly Regex ActionRegex = new(
        @"\[ACTIONS:\s*(\[.*?\])\s*\]",
        RegexOptions.Singleline | RegexOptions.Compiled);

    /// <summary>根据实体内容，用 AI 生成贴合当前知识图谱节点的快捷探索问题，替换静态预设提示。</summary>
    private async Task GenerateAiPromptsAsync(GraphNode node)
    {
        if (string.IsNullOrWhiteSpace(node.Name) || string.IsNullOrWhiteSpace(node.Collection))
            return;

        try
        {
            // 构建实体上下文摘要
            var relationsSummary = SelectedNodeRelations.Count > 0
                ? string.Join("；", SelectedNodeRelations.Take(8).Select(r => $"{r.Relation} → {r.ToName}({r.ToType})"))
                : "暂无关联";

            var snippetsSummary = ContextSnippets.Count > 0
                ? string.Join("\n", ContextSnippets.Take(3).Select(s => s.Content?.Trim()).Where(c => !string.IsNullOrWhiteSpace(c)).Select(c => c!.Length > 120 ? c[..120] + "…" : c))
                : "暂无摘要";

            // 限制上下文长度
            if (snippetsSummary.Length > 600)
                snippetsSummary = snippetsSummary[..600] + "…";

            // 构造 AI 查询：要求只输出 [ACTIONS: [...]] 格式的问题列表
            var query = $"请为知识图谱实体「{node.Name}」（类型：{node.Type}）生成3-4个深度探索问题。\n\n"
                      + $"关联关系：{relationsSummary}\n\n"
                      + $"知识片段摘要：{snippetsSummary}\n\n"
                      + "要求：问题需结合实体名称与关联知识，具体、有深度，每个问题不超过20字。"
                      + "请只以 [ACTIONS: [\"👉 问题1\", \"👉 问题2\", \"👉 问题3\"]] 格式输出，不包含其他文字。";

            var req = new ChatRequest
            {
                Query = query,
                Collection = node.Collection,
                TopK = 1, // 最小 RAG，主要靠 EntityContext 传递实体信息
                EnableWebSearch = false,
                EntityContext = $"实体: {node.Name}\n分类: {node.Type}\n关联关系: {relationsSummary}",
                Model = GetActiveModel(),
                ProviderConfig = GetActiveProviderConfig()
            };

            var resp = await _apiService.ChatAsync(req);

            if (string.IsNullOrWhiteSpace(resp?.Answer))
                return;

            // 从回答中解析 [ACTIONS: [...]]
            var match = ActionRegex.Match(resp.Answer);
            if (!match.Success)
                return;

            var json = match.Groups[1].Value;
            var list = JsonSerializer.Deserialize<List<string>>(json);

            if (list == null || list.Count == 0)
                return;

            // 确保每个问题带 emoji 前缀
            var emojis = new[] { "🔍", "💡", "🧩", "🎯" };
            var enriched = list.Select((q, i) =>
            {
var trimmed = q.Trim().TrimStart(' ', '：', ':');
                // 去掉可选的 "👉" 前缀（字符串级处理）
                if (trimmed.StartsWith("👉"))
                    trimmed = trimmed[2..].TrimStart();
                var hasEmoji = trimmed.Length > 0 && char.IsHighSurrogate(trimmed[0]);
                return hasEmoji
                    ? trimmed
                    : $"{emojis[i % emojis.Length]} {trimmed}";
            }).ToList();

            // 回到 UI 线程更新
            await Application.Current.Dispatcher.InvokeAsync(() =>
            {
                AdaptiveQuickPrompts.Clear();
                foreach (var item in enriched)
                {
                    AdaptiveQuickPrompts.Add(item);
                }
            });
        }
        catch (Exception ex)
        {
            // AI 生成失败时静默保留静态预设提示
            DebugLog.Warn($"AI 生成快捷探索问题失败: {ex.Message}", "GraphVM");
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
            IsLoading = true,
            IsWaitingForFirstToken = true,
            IsThinkingInProgress = true
        };
        assistantMsg.SourceMarkerRequested += index =>
        {
            var src = assistantMsg.Sources?.FirstOrDefault(s => s.Index == index);
            if (src != null)
            {
                OpenSource(src);
            }
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
            WebSearchMode = IsWebSearchEnabled ? SelectedWebSearchMode.Key : null,
            EntityContext = entityContext,
            Model = GetActiveModel(),
            ProviderConfig = GetActiveProviderConfig(),
            GithubToken = string.IsNullOrWhiteSpace(_appSettings?.GithubToken)
                ? null
                : _appSettings.GithubToken.Trim()
        };

        try
        {
            var sw = System.Diagnostics.Stopwatch.StartNew();
            var timerCts = new CancellationTokenSource();
            _ = Task.Run(async () =>
            {
                try
                {
                    while (!timerCts.Token.IsCancellationRequested && assistantMsg.IsLoading && (assistantMsg.IsThinkingInProgress || assistantMsg.IsWaitingForFirstToken))
                    {
                        await Task.Delay(100, timerCts.Token).ConfigureAwait(false);
                        if (timerCts.Token.IsCancellationRequested) break;
                        var curMs = sw.ElapsedMilliseconds;
                        System.Windows.Application.Current?.Dispatcher?.InvokeAsync(() =>
                        {
                            assistantMsg.UpdateLiveThinkingDuration(curMs);
                        });
                    }
                }
                catch { /* 取消时静默退出 */ }
            }, timerCts.Token);

            // 本条消息是否已落地过首个正文 token。
            // 不能用 IsWaitingForFirstToken / IsThinkingInProgress 代替：这两个标志会被
            // onStatus/onThinking 回调反复置 True（模型推理链、重试状态帧、Agent 自省帧），
            // 流中途一旦复位，下一个 token 会把已累积的正文整体覆盖，只剩最后一段。
            var firstTokenApplied = false;

            var streamResult = await _apiService.ChatStreamAsync(
                chatReq,
                onToken: token =>
                {
                    try { timerCts.Cancel(); } catch { }
                    System.Windows.Application.Current?.Dispatcher?.Invoke(() =>
                    {
                        if (!firstTokenApplied)
                        {
                            firstTokenApplied = true;
                            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
                            assistantMsg.Content = token;
                        }
                        else
                        {
                            assistantMsg.Content += token;
                        }
                        assistantMsg.TokenCount++;
                    });
                },
                onStatus: status =>
                {
                    System.Windows.Application.Current?.Dispatcher?.Invoke(() =>
                    {
                        assistantMsg.IsThinkingInProgress = true;
                        assistantMsg.UpdateLiveThinkingDuration(sw.ElapsedMilliseconds);
                        assistantMsg.AddThinkingStep(status ?? "");
                        assistantMsg.ShowStatus = true;
                        assistantMsg.StatusText = status ?? "";
                    });
                },
                onThinking: thinking =>
                {
                    System.Windows.Application.Current?.Dispatcher?.Invoke(() =>
                    {
                        assistantMsg.IsThinkingInProgress = true;
                        assistantMsg.UpdateLiveThinkingDuration(sw.ElapsedMilliseconds);
                        assistantMsg.AppendThinking(thinking ?? "");
                    });
                },
                onRestart: () =>
                {
                    // 后端精简上下文后重新生成：丢弃上一次尝试的废弃半成品
                    System.Windows.Application.Current?.Dispatcher?.Invoke(() =>
                    {
                        firstTokenApplied = false;
                        assistantMsg.Content = string.Empty;
                    });
                },
                onDone: doneResult =>
                {
                    try { timerCts.Cancel(); } catch { }
                    System.Windows.Application.Current?.Dispatcher?.Invoke(() =>
                    {
                        assistantMsg.IsLoading = false;
                        assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
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
                ct: ct
            );

            if (assistantMsg.Sources == null || assistantMsg.Sources.Count == 0)
            {
                assistantMsg.Sources = streamResult.Sources;
            }
        }
        catch (OperationCanceledException)
        {
            assistantMsg.IsLoading = false;
            assistantMsg.IsThinkingInProgress = false;
            assistantMsg.IsWaitingForFirstToken = false;
            assistantMsg.Content += "\n\n*(已手动停止生成)*";
            assistantMsg.ForceRefreshRender();
        }
        catch (Exception ex)
        {
            assistantMsg.IsLoading = false;
            assistantMsg.IsThinkingInProgress = false;
            assistantMsg.IsWaitingForFirstToken = false;
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

    /// <summary>打开来源详情（Web来源用默认浏览器打开，本地来源提示可在搜索页查证）。</summary>
    [RelayCommand]
    public void OpenSource(SourceRef? source)
    {
        if (source == null) return;
        if (source.IsWebSource && !string.IsNullOrWhiteSpace(source.Url) &&
            Uri.TryCreate(source.Url, UriKind.Absolute, out var uri) &&
            (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps))
        {
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"打开 Web 来源失败: {ex.Message}", "GraphVM");
            }
        }
    }

    /// <summary>撤回并回填实体提问。</summary>
    [RelayCommand]
    public void Withdraw(ChatMessage? message = null)
    {
        if (IsEntityAiGenerating || EntityChatMessages.Count == 0) return;
        var target = message;
        if (target == null)
        {
            target = EntityChatMessages.LastOrDefault(m => m.Role == "user");
        }
        if (target != null && target.Role == "user")
        {
            var idx = EntityChatMessages.IndexOf(target);
            if (idx >= 0)
            {
                EntityChatInput = target.Content;
                while (EntityChatMessages.Count > idx)
                {
                    EntityChatMessages.RemoveAt(EntityChatMessages.Count - 1);
                }
                OnPropertyChanged(nameof(HasEntityChatMessages));
            }
        }
    }

    /// <summary>重新生成实体最后一条回答。</summary>
    [RelayCommand]
    public void Regenerate()
    {
        if (IsEntityAiGenerating || EntityChatMessages.Count == 0) return;
        var lastUserIdx = -1;
        for (var i = EntityChatMessages.Count - 1; i >= 0; i--)
        {
            if (EntityChatMessages[i].Role == "user")
            {
                lastUserIdx = i;
                break;
            }
        }
        if (lastUserIdx < 0) return;
        var query = EntityChatMessages[lastUserIdx].Content;
        while (EntityChatMessages.Count > lastUserIdx)
        {
            EntityChatMessages.RemoveAt(EntityChatMessages.Count - 1);
        }
        _ = SendEntityChatAsync(query);
    }

    /// <summary>执行下一步建议行动。</summary>
    [RelayCommand]
    public void ExecuteAction(string? action)
    {
        if (!string.IsNullOrWhiteSpace(action))
        {
            _ = SendEntityChatAsync(action.Trim());
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

    // --- 沉淀入库弹窗微调状态 (方案 A + B) ---
    private bool _isIngestDialogOpen;
    private string _ingestDialogTitle = string.Empty;
    private string _ingestDialogContent = string.Empty;
    private string _ingestDialogCollection = "default";
    private string _ingestDialogTags = string.Empty;
    private bool _isDialogIngesting;
    private ChatMessage? _currentIngestingMessage;

    public bool IsIngestDialogOpen
    {
        get => _isIngestDialogOpen;
        set => SetProperty(ref _isIngestDialogOpen, value);
    }

    public string IngestDialogTitle
    {
        get => _ingestDialogTitle;
        set => SetProperty(ref _ingestDialogTitle, value);
    }

    public string IngestDialogContent
    {
        get => _ingestDialogContent;
        set => SetProperty(ref _ingestDialogContent, value);
    }

    public string IngestDialogCollection
    {
        get => _ingestDialogCollection;
        set => SetProperty(ref _ingestDialogCollection, value);
    }

    public string IngestDialogTags
    {
        get => _ingestDialogTags;
        set => SetProperty(ref _ingestDialogTags, value);
    }

    public bool IsDialogIngesting
    {
        get => _isDialogIngesting;
        set => SetProperty(ref _isDialogIngesting, value);
    }

    /// <summary>打开沉淀微调弹窗（支持整条消息或划词选中片段）。</summary>
    [RelayCommand]
    public void OpenIngestDialog(object? param)
    {
        string contentToIngest = string.Empty;
        _currentIngestingMessage = null;

        if (param is ChatMessage msg)
        {
            _currentIngestingMessage = msg;
            contentToIngest = msg.Content.Trim();
            var actionIdx = contentToIngest.IndexOf("[ACTIONS:", StringComparison.OrdinalIgnoreCase);
            if (actionIdx >= 0)
            {
                contentToIngest = contentToIngest[..actionIdx].Trim();
            }
        }
        else if (param is string str && !string.IsNullOrWhiteSpace(str))
        {
            contentToIngest = str.Trim();
        }

        if (string.IsNullOrWhiteSpace(contentToIngest))
            return;

        var entityPrefix = SelectedNode != null ? $"【{SelectedNode.Name}】" : "";
        var firstLine = contentToIngest.Split('\n', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? contentToIngest;
        firstLine = System.Text.RegularExpressions.Regex.Replace(firstLine, @"^[#\s\-*📌💡]+", "").Trim();
        var initialTitle = firstLine.Length > 25 ? firstLine[..25] + "…" : firstLine;
        if (string.IsNullOrWhiteSpace(initialTitle))
        {
            initialTitle = $"实体探讨沉淀 ({DateTime.Now:MM-dd HH:mm})";
        }

        IngestDialogTitle = $"{entityPrefix}{initialTitle}";
        IngestDialogContent = contentToIngest;
        IngestDialogTags = SelectedNode != null ? $"图谱实体, {SelectedNode.Name}, 架构笔记" : "图谱实体, 架构笔记";

        var targetCollection = SelectedNode?.Collection ?? Collection;
        if (string.IsNullOrWhiteSpace(targetCollection) || targetCollection == "全部集合")
        {
            targetCollection = "default";
        }
        IngestDialogCollection = targetCollection;

        IsIngestDialogOpen = true;
    }

    /// <summary>确认沉淀入库（带用户微调后的标题、集合与正文）。</summary>
    [RelayCommand]
    public async Task ConfirmIngestDialogAsync()
    {
        if (string.IsNullOrWhiteSpace(IngestDialogContent) || IsDialogIngesting)
            return;

        IsDialogIngesting = true;
        try
        {
            var text = IngestDialogContent.Trim();
            var title = string.IsNullOrWhiteSpace(IngestDialogTitle) ? "实体沉淀笔记" : IngestDialogTitle.Trim();
            var collection = string.IsNullOrWhiteSpace(IngestDialogCollection) ? "default" : IngestDialogCollection.Trim();

            if (!string.IsNullOrWhiteSpace(IngestDialogTags))
            {
                var tagList = IngestDialogTags.Split(new[] { ',', ' ', '，', ';' }, StringSplitOptions.RemoveEmptyEntries)
                    .Select(t => t.StartsWith("#") ? t : $"#{t}")
                    .ToList();
                if (tagList.Count > 0)
                {
                    text = $"【标签】：{string.Join(" ", tagList)}\n\n{text}";
                }
            }

            var req = new IngestTextRequest
            {
                Text = text,
                Title = $"💡 {title}",
                Collection = collection,
                Force = true
            };

            await _apiService.IngestTextAsync(req);

            if (_currentIngestingMessage != null)
            {
                _currentIngestingMessage.IsIngested = true;
            }

            IsIngestDialogOpen = false;
            _notifications?.Success($"已将内容沉淀至集合「{collection}」", "沉淀入库");
            DebugLog.Info($"图谱对话沉淀入库成功: title={title}, collection={collection}", "GraphVM");
        }
        catch (Exception ex)
        {
            _notifications?.Error($"沉淀入库失败: {ex.Message}", "错误");
            DebugLog.Error($"图谱对话沉淀入库异常: {ex}", "GraphVM");
        }
        finally
        {
            IsDialogIngesting = false;
        }
    }

    /// <summary>关闭沉淀弹窗。</summary>
    [RelayCommand]
    public void CloseIngestDialog()
    {
        IsIngestDialogOpen = false;
    }

    /// <summary>一键将图谱对话中的回答沉淀为知识笔记入库（打开微调弹窗）。</summary>
    [RelayCommand]
    public void IngestMessage(ChatMessage? message)
    {
        OpenIngestDialog(message);
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
                WebReferences = webRefs,
                Model = GetActiveModel(),
                ProviderConfig = GetActiveProviderConfig()
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
            var msg = ex.Message;
            if (!msg.StartsWith("知识卡片蒸馏失败", StringComparison.OrdinalIgnoreCase))
            {
                msg = $"知识卡片蒸馏失败: {msg}";
            }
            _notifications?.Error(msg, "错误");
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

    private string? GetActiveModel()
    {
        if (_appSettings == null) return null;
        if (!string.IsNullOrWhiteSpace(_appSettings.ActiveProfileId) && _appSettings.LlmProfiles is { Count: > 0 })
        {
            var activeProfile = _appSettings.LlmProfiles.FirstOrDefault(p => p.Id == _appSettings.ActiveProfileId);
            if (!string.IsNullOrWhiteSpace(activeProfile?.Model))
            {
                return activeProfile.Model;
            }
        }
        return string.IsNullOrWhiteSpace(_appSettings.LlmModel) ? null : _appSettings.LlmModel;
    }

    private ProviderConfig? GetActiveProviderConfig()
    {
        if (_appSettings == null) return null;
        if (!string.IsNullOrWhiteSpace(_appSettings.ActiveProfileId) && _appSettings.LlmProfiles is { Count: > 0 })
        {
            var activeProfile = _appSettings.LlmProfiles.FirstOrDefault(p => p.Id == _appSettings.ActiveProfileId);
            if (activeProfile != null && !string.IsNullOrWhiteSpace(activeProfile.Provider) && activeProfile.Provider != "none")
            {
                return new ProviderConfig
                {
                    Provider = activeProfile.Provider,
                    ApiKey = activeProfile.ApiKey,
                    BaseUrl = activeProfile.BaseUrl,
                    Model = activeProfile.Model,
                    Temperature = activeProfile.Temperature,
                    MaxTokens = activeProfile.MaxTokens
                };
            }
        }

        if (!string.IsNullOrWhiteSpace(_appSettings.LlmProvider) && _appSettings.LlmProvider != "none")
        {
            return new ProviderConfig
            {
                Provider = _appSettings.LlmProvider,
                ApiKey = _appSettings.LlmApiKey,
                BaseUrl = _appSettings.LlmBaseUrl,
                Model = _appSettings.LlmModel,
                Temperature = _appSettings.LlmTemperature,
                MaxTokens = _appSettings.LlmMaxTokens
            };
        }

        return null;
    }
}
