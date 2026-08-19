using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;
using Markdig;
using Markdig.Wpf;

namespace DocMind.ViewModels;

/// <summary>单条对话消息（用户或助手）。可变 class 以支持流式增量追加 token。</summary>
public sealed partial class ChatMessage : System.ComponentModel.INotifyPropertyChanged
{
    private string _role = string.Empty;
    private bool _isIngested;
    private bool _isIngesting;

    /// <summary>是否已沉淀入知识库。</summary>
    public bool IsIngested
    {
        get => _isIngested;
        set => SetField(ref _isIngested, value);
    }

    /// <summary>是否正在沉淀入库中。</summary>
    public bool IsIngesting
    {
        get => _isIngesting;
        set => SetField(ref _isIngesting, value);
    }
    private string _content = string.Empty;
    private FlowDocument? _renderedDocument;
    private long _lastRenderTicks;
    /// <summary>reparse 节流间隔(ms):流式期间避免每个 token 都重新解析 Markdown。</summary>
    private const long RenderThrottleMs = 50;
    private IReadOnlyList<SourceRef>? _sources;
    private string? _model;
    private string? _provider;
    private int? _elapsedMs;
    private bool _isLoading;
    private bool _isWaitingForFirstToken;
    private bool _showRegenerate;
    private bool _showWithdraw;
    private string _waitingHint = "🧠 正在检索知识库并思考回答...";

    /// <summary>角色：user / assistant / system。</summary>
    public string Role
    {
        get => _role;
        set
        {
            if (SetField(ref _role, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(IsUser)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(IsAssistant)));
            }
        }
    }

    /// <summary>消息内容。</summary>
    public string Content
    {
        get => _content;
        set
        {
            if (SetField(ref _content, value))
            {
                UpdateRenderedDocument();
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(CanCopy)));
            }
        }
    }

    /// <summary>助手回答经 Markdig 解析后的 FlowDocument;用户消息或解析失败时为 null。</summary>
    /// <remarks>UI 层据此渲染 Markdown(代码块/列表/表格/可点击链接);用户消息仍走纯 TextBlock。</remarks>
    public FlowDocument? RenderedDocument
    {
        get => _renderedDocument;
        private set => SetField(ref _renderedDocument, value);
    }

    /// <summary>强制重新解析 Markdown(终帧 onDone 后调用,确保最终渲染完整,不受节流影响)。</summary>
    public void ForceRefreshRender() => UpdateRenderedDocument(force: true);

    /// <summary>流式增量追加 token。</summary>
    public void AppendToken(string token)
    {
        Content += token;
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(CanCopy)));
    }

    private static readonly System.Text.RegularExpressions.Regex ActionRegex =
        new(@"\[ACTIONS:\s*(\[.*?\])\s*\]", System.Text.RegularExpressions.RegexOptions.Singleline | System.Text.RegularExpressions.RegexOptions.Compiled);

    private static readonly System.Text.RegularExpressions.Regex ArtifactRegex =
        new(@":::\s*artifact(?:\s+type=[""']?([a-zA-Z0-9_-]+)[""']?)?(?:\s+title=[""']?([^""'\n\r]+)[""']?)?(?:\s+theme=[""']?([a-zA-Z0-9_-]+)[""']?)?\s*\n([\s\S]*?)(?::::|\Z)", System.Text.RegularExpressions.RegexOptions.Singleline | System.Text.RegularExpressions.RegexOptions.Compiled);

    /// <summary>AI 根据上下文预测的下一步行动建议列表。</summary>
    public ObservableCollection<string> FollowUpActions { get; } = new();

    /// <summary>是否有下一步行动建议。</summary>
    public bool HasFollowUpActions => FollowUpActions.Count > 0;

    private ArtifactItem? _artifact;

    /// <summary>消息内包含的结构化创作交付物（PPTX/DOCX/XLSX/HTML）。</summary>
    public ArtifactItem? Artifact
    {
        get => _artifact;
        set
        {
            if (SetField(ref _artifact, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasArtifact)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ArtifactTitle)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ArtifactBadgeText)));
            }
        }
    }

    /// <summary>是否有创作交付物。</summary>
    public bool HasArtifact => Artifact != null;

    /// <summary>创作交付物标题。</summary>
    public string ArtifactTitle => Artifact?.Title ?? "创作物";

    /// <summary>创作交付物徽章文案。</summary>
    public string ArtifactBadgeText => Artifact switch
    {
        { IsPpt: true } => $"📊 PPT 演示文稿（{Artifact.SlideCount} 页）",
        { IsDoc: true } => "📄 深度研报 / 公文方案",
        { IsExcel: true } => "📑 结构化数据对比表",
        { IsHtml: true } => "🌐 交互式知识看板",
        _ => "📦 创作交付物",
    };

    /// <summary>把 Content 用 Markdig 解析为 FlowDocument。流式期间节流,终帧后强制刷新。</summary>
    private void UpdateRenderedDocument(bool force = false)
    {
        // 确保必须在 UI 线程创建与修改 FlowDocument 和 FollowUpActions，防止多线程跨线程访问崩溃
        if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
        {
            dispatcher.InvokeAsync(() => UpdateRenderedDocument(force));
            return;
        }

        // 用户消息不渲染 Markdown(纯文本即可,避免 Markdown 语法误解析)
        if (IsUser || string.IsNullOrEmpty(Content))
        {
            if (_renderedDocument is not null)
            {
                RenderedDocument = null;
            }
            return;
        }
        // 节流:流式期间频繁 reparse 浪费 CPU,50ms 一次足够流畅
        var now = Environment.TickCount64;
        if (!force && (now - _lastRenderTicks) < RenderThrottleMs)
        {
            return;
        }
        _lastRenderTicks = now;
        try
        {
            var rawContent = Content;
            var cleanContent = rawContent;

            // 1. 嗅探提取 Artifact 交付物
            var artMatch = ArtifactRegex.Match(rawContent);
            if (artMatch.Success)
            {
                var aType = artMatch.Groups[1].Value;
                var aTitle = artMatch.Groups[2].Value;
                var aTheme = artMatch.Groups[3].Value;
                var aBody = artMatch.Groups[4].Value;

                if (string.IsNullOrWhiteSpace(aType)) aType = "docx";
                if (string.IsNullOrWhiteSpace(aTitle)) aTitle = "知识创作交付物";
                if (string.IsNullOrWhiteSpace(aTheme)) aTheme = "tech_blue";

                var item = new ArtifactItem
                {
                    Type = aType.ToLowerInvariant().Trim(),
                    Title = aTitle.Trim(),
                    Theme = aTheme.ToLowerInvariant().Trim(),
                    RawContent = aBody.Trim(),
                };

                // PPT 幻灯片切片解析
                if (item.IsPpt)
                {
                    var pages = System.Text.RegularExpressions.Regex.Split(item.RawContent, @"(?m)^---\s*$");
                    if (pages.Length <= 1)
                    {
                        var splitByH1 = System.Text.RegularExpressions.Regex.Split(item.RawContent, @"(?m)^(?=#\s+)");
                        var candidates = splitByH1.Where(p => !string.IsNullOrWhiteSpace(p)).ToArray();
                        if (candidates.Length >= 2) pages = candidates;
                    }
                    var sIndex = 1;
                    foreach (var page in pages)
                    {
                        var pClean = page.Trim();
                        if (string.IsNullOrWhiteSpace(pClean)) continue;

                        var sTitle = $"第 {sIndex} 页";
                        var sSub = "";
                        var sLayout = "general";
                        var sBullets = new List<string>();
                        var sNotes = "";
                        var sCards = new List<SlideCardItem>();
                        var sMetrics = new List<MetricItem>();
                        var sTimeline = new List<TimelineNodeItem>();
                        var sQuote = "";
                        var sTable = new List<List<string>>();

                        // 提取备注
                        var noteMatch = System.Text.RegularExpressions.Regex.Match(pClean, @"<!--\s*note:\s*([\s\S]*?)-->", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
                        if (noteMatch.Success)
                        {
                            sNotes = noteMatch.Groups[1].Value.Trim();
                            pClean = pClean.Remove(noteMatch.Index, noteMatch.Length).Trim();
                        }

                        // 提取显式板式
                        var layoutMatch = System.Text.RegularExpressions.Regex.Match(pClean, @"<!--\s*layout:\s*([a-zA-Z0-9_-]+)\s*-->", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
                        if (layoutMatch.Success)
                        {
                            sLayout = layoutMatch.Groups[1].Value.ToLowerInvariant().Trim();
                            pClean = pClean.Remove(layoutMatch.Index, layoutMatch.Length).Trim();
                        }

                        SlideCardItem? curCard = null;
                        foreach (var l in pClean.Split('\n'))
                        {
                            var ls = l.Trim();
                            if (string.IsNullOrWhiteSpace(ls)) continue;

                            if (ls.StartsWith("# ") && sTitle == $"第 {sIndex} 页")
                            {
                                sTitle = ls[2..].Trim();
                            }
                            else if (ls.StartsWith("## ") && sIndex == 1 && string.IsNullOrEmpty(sSub))
                            {
                                sSub = ls[3..].Trim();
                            }
                            else if (ls.StartsWith("### "))
                            {
                                if (curCard != null) sCards.Add(curCard);
                                curCard = new SlideCardItem { Title = ls[4..].Trim() };
                            }
                            else if (ls.StartsWith(">"))
                            {
                                var q = ls.TrimStart('>', ' ').Trim();
                                sQuote = string.IsNullOrEmpty(sQuote) ? q : sQuote + "\n" + q;
                            }
                            else if (ls.StartsWith("|") && ls.EndsWith("|"))
                            {
                                if (!System.Text.RegularExpressions.Regex.IsMatch(ls, @"^\|[\s\-:|]+\|$"))
                                {
                                    var cols = ls.Trim('|').Split('|').Select(c => c.Trim()).ToList();
                                    sTable.Add(cols);
                                }
                            }
                            else if (ls.StartsWith("- ") || ls.StartsWith("* ") || ls.StartsWith("+ ") || ls.StartsWith("• "))
                            {
                                var b = ls[2..].Trim();
                                if (curCard != null) curCard.Bullets.Add(b);
                                else sBullets.Add(b);
                            }
                            else if (System.Text.RegularExpressions.Regex.IsMatch(ls, @"^\d+\.\s+"))
                            {
                                var b = System.Text.RegularExpressions.Regex.Replace(ls, @"^\d+\.\s+", "").Trim();
                                if (curCard != null) curCard.Bullets.Add(b);
                                else sBullets.Add(b);
                            }
                            else if (!ls.StartsWith("#") && !ls.StartsWith("<!--"))
                            {
                                if (curCard != null)
                                {
                                    if (string.IsNullOrEmpty(curCard.Content)) curCard.Content = ls;
                                    else curCard.Bullets.Add(ls);
                                }
                                else if (ls.Length < 120)
                                {
                                    sBullets.Add(ls);
                                }
                            }
                        }

                        if (curCard != null) sCards.Add(curCard);

                        // 启发式指标抽取
                        var metricRx = new System.Text.RegularExpressions.Regex(@"^([0-9]+(?:\.[0-9]+)?(?:%|x|X|ms|s|MB|GB|KB|倍|万|亿)?)\s*[:：\-—]\s*(.*)$");
                        foreach (var b in sBullets)
                        {
                            var mm = metricRx.Match(b);
                            if (mm.Success) sMetrics.Add(new MetricItem { Value = mm.Groups[1].Value.Trim(), Label = mm.Groups[2].Value.Trim() });
                        }

                        // 启发式时间线抽取
                        var timeRx = new System.Text.RegularExpressions.Regex(@"^(阶段[一二三四五六七八九十1-9]|Step\s*\d+|Q[1-4]|步骤[1-9])\s*[:：\-—]\s*(.*)$", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
                        foreach (var b in sBullets)
                        {
                            var tm = timeRx.Match(b);
                            if (tm.Success) sTimeline.Add(new TimelineNodeItem { Stage = tm.Groups[1].Value.Trim(), Title = tm.Groups[2].Value.Trim() });
                        }

                        // 板式智能裁决
                        if (sLayout == "general")
                        {
                            if (sIndex == 1) sLayout = "cover";
                            else if (sCards.Count >= 2 && sCards.Count <= 4) sLayout = "cards";
                            else if (sMetrics.Count >= 2 && sMetrics.Count == sBullets.Count) sLayout = "metrics";
                            else if (sTimeline.Count >= 2 && sTimeline.Count == sBullets.Count) sLayout = "timeline";
                            else if (sTable.Count >= 2) sLayout = "table";
                            else if (!string.IsNullOrEmpty(sQuote)) sLayout = "quote";
                        }

                        item.Slides.Add(new SlideItem
                        {
                            Index = sIndex,
                            Title = sTitle,
                            Subtitle = sSub,
                            Layout = sLayout,
                            BulletPoints = sBullets,
                            SpeakerNotes = sNotes,
                            Cards = sCards,
                            Metrics = sMetrics,
                            TimelineNodes = sTimeline,
                            QuoteText = sQuote,
                            TableData = sTable.Count > 0 ? sTable : null,
                        });
                        sIndex++;
                    }
                }

                Artifact = item;
            }

            var match = ActionRegex.Match(cleanContent);
            if (match.Success)
            {
                cleanContent = cleanContent.Remove(match.Index, match.Length).TrimEnd();
                try
                {
                    var json = match.Groups[1].Value;
                    var list = System.Text.Json.JsonSerializer.Deserialize<List<string>>(json);
                    if (list != null && list.Count > 0)
                    {
                        if (FollowUpActions.Count != list.Count || !FollowUpActions.SequenceEqual(list))
                        {
                            FollowUpActions.Clear();
                            foreach (var item in list)
                            {
                                if (!string.IsNullOrWhiteSpace(item))
                                {
                                    FollowUpActions.Add(item.Trim());
                                }
                            }
                            PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasFollowUpActions)));
                        }
                    }
                }
                catch
                {
                    var items = System.Text.RegularExpressions.Regex.Matches(match.Groups[1].Value, "\"([^\"]+)\"");
                    if (items.Count > 0)
                    {
                        FollowUpActions.Clear();
                        foreach (System.Text.RegularExpressions.Match item in items)
                        {
                            FollowUpActions.Add(item.Groups[1].Value.Trim());
                        }
                        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasFollowUpActions)));
                    }
                }
            }
            else
            {
                var partialIdx = cleanContent.LastIndexOf("[ACTIONS:", StringComparison.OrdinalIgnoreCase);
                if (partialIdx >= 0 && partialIdx > cleanContent.Length - 120)
                {
                    cleanContent = cleanContent[..partialIdx].TrimEnd();
                }
            }

            var pipeline = new MarkdownPipelineBuilder().UseSupportedExtensions().Build();
            var doc = Markdig.Wpf.Markdown.ToFlowDocument(cleanContent, pipeline);
            // 对齐 ChatView 气泡样式:清除 FlowDocument 默认页边距,继承 BodyText 样式(FontSize=15, Medium, TextPrimary)
            doc.PagePadding = new Thickness(0);
            doc.FontFamily = SystemFonts.MessageFontFamily;
            doc.FontSize = 15;
            doc.FontWeight = FontWeights.Medium;
            doc.Foreground = (Brush)(System.Windows.Application.Current?.FindResource("TextPrimaryBrush")
                                     ?? Brushes.Black);
            RenderedDocument = doc;
        }
        catch (Exception ex)
        {
            // 解析失败:清空 RenderedDocument,UI 会 fallback 到纯 TextBlock(由 Visibility 控制)
            DebugLog.Warn($"Markdown 解析失败,降级纯文本: {ex.Message}", "Chat");
            if (_renderedDocument is not null)
            {
                RenderedDocument = null;
            }
        }
    }

    /// <summary>引用来源（仅 assistant 有）。</summary>
    public IReadOnlyList<SourceRef>? Sources
    {
        get => _sources;
        set => SetField(ref _sources, value);
    }

    /// <summary>模型名（仅 assistant 有）。</summary>
    public string? Model
    {
        get => _model;
        set => SetField(ref _model, value);
    }

    /// <summary>提供商标识（仅 assistant 有）。</summary>
    public string? Provider
    {
        get => _provider;
        set => SetField(ref _provider, value);
    }

    /// <summary>耗时 ms（仅 assistant 有）。</summary>
    public int? ElapsedMs
    {
        get => _elapsedMs;
        set => SetField(ref _elapsedMs, value);
    }

    /// <summary>是否正在加载。整体生成期间为 true。</summary>
    public bool IsLoading
    {
        get => _isLoading;
        set
        {
            if (SetField(ref _isLoading, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(CanCopy)));
            }
        }
    }

    /// <summary>是否正在等待首个 token返回（控制骨架屏可见性）。</summary>
    public bool IsWaitingForFirstToken
    {
        get => _isWaitingForFirstToken;
        set => SetField(ref _isWaitingForFirstToken, value);
    }

    /// <summary>是否显示「重新生成」（仅最后一条 assistant 消息、非生成中；由 VM 维护）。</summary>
    public bool ShowRegenerate
    {
        get => _showRegenerate;
        set => SetField(ref _showRegenerate, value);
    }

    /// <summary>是否显示「撤回」按钮（仅最后一条用户消息在非生成中显示）。</summary>
    public bool ShowWithdraw
    {
        get => _showWithdraw;
        set => SetField(ref _showWithdraw, value);
    }

    /// <summary>等待首字或非流式大包期间的动态状态提示文案。</summary>
    public string WaitingHint
    {
        get => _waitingHint;
        set => SetField(ref _waitingHint, value);
    }

    /// <summary>是否有可复制内容（控制「复制」按钮可见性）。</summary>
    public bool CanCopy => !IsLoading && !string.IsNullOrEmpty(Content);

    /// <summary>复制消息内容到剪贴板。</summary>
    [RelayCommand]
    private void Copy()
    {
        if (string.IsNullOrEmpty(Content))
        {
            return;
        }
        try
        {
            Clipboard.SetText(Content);
        }
        catch (Exception ex)
        {
            // 剪贴板被其他进程占用（OCR/远程桌面场景偶发）：重试一次，仍失败仅记录
            DebugLog.Warn($"复制到剪贴板失败: {ex.Message}", "Chat");
            try { Clipboard.SetText(Content); } catch { /* 放弃，不打断 UI */ }
        }
    }

    /// <summary>是否有引用来源（控制来源列表可见性）。</summary>
    public bool HasSources => Sources is { Count: > 0 };

    /// <summary>是否有模型信息（仅在 assistant 回答中显示）。</summary>
    public bool HasModel => Model is not null;

    /// <summary>是否来自用户（UI 分左右用）。</summary>
    public bool IsUser => Role == "user";

    /// <summary>是否来自助手（UI 渲染助手 Markdown 消息卡片用）。</summary>
    public bool IsAssistant => Role == "assistant";

    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;

    private bool SetField<T>(ref T field, T value, [System.Runtime.CompilerServices.CallerMemberName] string? propertyName = null)
    {
        if (!System.Collections.Generic.EqualityComparer<T>.Default.Equals(field, value))
        {
            field = value;
            PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(propertyName));
            return true;
        }
        return false;
    }
}

/// <summary>可勾选的知识库集合项（复选框用）。</summary>
public sealed class CollectionItem : System.ComponentModel.INotifyPropertyChanged
{
    private string _name = string.Empty;
    private bool _isSelected;

    public string Name
    {
        get => _name;
        set
        {
            if (_name != value)
            {
                _name = value;
                OnPropertyChanged(nameof(Name));
            }
        }
    }

    public bool IsSelected
    {
        get => _isSelected;
        set
        {
            if (_isSelected != value)
            {
                _isSelected = value;
                OnPropertyChanged(nameof(IsSelected));
            }
        }
    }

    public event System.ComponentModel.PropertyChangedEventHandler? PropertyChanged;

    private void OnPropertyChanged(string propertyName)
        => PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(propertyName));
}

/// <summary>办公角色人设选项。</summary>
public sealed record PersonaOption(string Id, string DisplayName, string Icon, string Description)
{
    public override string ToString() => DisplayName;
}

/// <summary>历史会话列表项（ComboBox 显示用）。</summary>
public sealed class ChatSessionItem
{
    public string ChatId { get; init; } = string.Empty;

    /// <summary>会话标题（首条用户问题前 50 字）。</summary>
    public string Title { get; init; } = string.Empty;

    public int MessageCount { get; init; }

    /// <summary>最后更新时间（ISO，来自后端）。</summary>
    public string UpdatedAt { get; init; } = string.Empty;

    /// <summary>下拉显示文本。</summary>
    public string Display
    {
        get
        {
            var title = string.IsNullOrWhiteSpace(Title) ? ChatId : Title;
            return MessageCount > 0 ? $"{title}（{MessageCount} 条）" : title;
        }
    }
}

public partial class ChatViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;
    private readonly NotificationService? _notifications;

    /// <summary>用户点击引用来源时的请求事件。MainViewModel 订阅后导航到搜索页并传入源文件名。</summary>
    public event Action<SourceRef>? SourceSearchRequested;

    /// <summary>用户点击「前往配置大模型」请求事件。MainViewModel 订阅后跳转到设置页。</summary>
    public event Action? NavigateToSettingsRequested;

    /// <summary>导航前往设置页。</summary>
    [RelayCommand]
    private void NavigateToSettings() => NavigateToSettingsRequested?.Invoke();

    private string _inputText = string.Empty;
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private string? _chatId;
    private bool _isLoadingSessions;

    /// <summary>模型下拉首项伪值：表示「用设置页配置的默认模型」。</summary>
    public const string DefaultModelLabel = "默认（设置页模型）";

    private string _selectedModel = DefaultModelLabel;

    /// <summary>可选的办公与创作角色人设列表。</summary>
    public IReadOnlyList<PersonaOption> AvailablePersonas { get; } = new List<PersonaOption>
    {
        new("office", "💼 知识办公助手", "💼", "提炼核心结论、梳理 Action Items 待办清单与标准公文润色"),
        new("ppt", "📊 PPT 演示架构师", "📊", "依托知识库生成 Marp 语法幻灯片、提炼分页要点与演讲备注"),
        new("doc", "📄 资深研报公文专家", "📄", "深度技术方案论证、公文撰写、行业研报与规范排版"),
        new("lesson", "🎓 课程教案设计师", "🎓", "教学大纲设计、课时环节编排、重难点剖析与随堂测验"),
        new("table", "📑 商业数据分析师", "📑", "多维对比矩阵抽取、指标打分表与甘特排期规划"),
        new("web", "🌐 交互看板工程师", "🌐", "生成自包含 HTML5 响应式知识总结看板与卡片"),
        new("architect", "🧠 资深系统架构师", "🧠", "系统设计模式选型、底层运行机制剖析、性能瓶颈评估与架构演进设计"),
        new("engineer", "🛠️ 资深研发工匠", "🛠️", "工业级代码实现、重构优化、异常边界防御与单元测试建议"),
        new("brainstorm", "💡 创新方案顾问", "💡", "头脑风暴、SWOT 矩阵分析、多方案多维度对比表格与排期落地规划"),
    };

    private PersonaOption _selectedPersona;

    /// <summary>当前选中的办公角色人设。</summary>
    public PersonaOption SelectedPersona
    {
        get => _selectedPersona;
        set
        {
            if (SetProperty(ref _selectedPersona, value ?? AvailablePersonas[0]))
            {
                StatusMessage = $"角色: {_selectedPersona.DisplayName}";
            }
        }
    }

    public ChatViewModel(IDoc2kbApiService apiService, NotificationService? notifications = null)
    {
        _apiService = apiService;
        _notifications = notifications;
        Title = "对话";
        _selectedPersona = AvailablePersonas[0];

        Collections = new ObservableCollection<CollectionItem>();
        Collections.CollectionChanged += (_, e) =>
        {
            if (e.NewItems is not null)
            {
                foreach (CollectionItem item in e.NewItems)
                {
                    item.PropertyChanged += OnCollectionItemChanged;
                }
            }
            if (e.OldItems is not null)
            {
                foreach (CollectionItem item in e.OldItems)
                {
                    item.PropertyChanged -= OnCollectionItemChanged;
                }
            }
            OnPropertyChanged(nameof(HasSelectedCollection));
            SendCommand.NotifyCanExecuteChanged();
        };
        LoadCollectionsCommand = new AsyncRelayCommand(LoadCollectionsAsync);
        AddCollectionCommand = new AsyncRelayCommand<string?>(AddCollectionAsync);

        Sessions = new ObservableCollection<ChatSessionItem>();
        AvailableModels = new ObservableCollection<string> { DefaultModelLabel };

        Messages.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(ShowEmptyGuide));
            OnPropertyChanged(nameof(EmptyGuideText));
            UpdateMessageFlags();
        };

        // 构造时拉取已有知识库集合 + 历史会话 + 后端当前模型（模型下拉种子）
        _ = LoadCollectionsAsync();
        _ = LoadSessionsAsync();
        _ = SeedModelFromConfigAsync();
    }

    /// <summary>可勾选的知识库集合列表（复选框）。</summary>
    public ObservableCollection<CollectionItem> Collections { get; }

    /// <summary>历史会话列表（持久化在后端 SQLite，重启可恢复）。</summary>
    public ObservableCollection<ChatSessionItem> Sessions { get; }

    /// <summary>模型下拉候选（首项为「默认」伪值，其余为拉取/种子模型）。</summary>
    public ObservableCollection<string> AvailableModels { get; }

    /// <summary>当前选中模型；DefaultModelLabel = 用设置页配置（请求不带 model）。</summary>
    public string SelectedModel
    {
        get => _selectedModel;
        set
        {
            if (SetProperty(ref _selectedModel, string.IsNullOrWhiteSpace(value) ? DefaultModelLabel : value))
            {
                StatusMessage = value == DefaultModelLabel ? "就绪" : $"模型: {value}（下条消息生效）";
            }
        }
    }

    private ChatSessionItem? _selectedSession;

    /// <summary>当前会话；null = 新会话（未发送过消息）。选中即载入历史消息并可续聊。</summary>
    public ChatSessionItem? SelectedSession
    {
        get => _selectedSession;
        set
        {
            if (SetProperty(ref _selectedSession, value) && value is not null && !_isLoadingSessions)
            {
                _ = LoadSessionMessagesAsync(value);
            }
        }
    }

    /// <summary>当前选中的集合名列表（供发送时使用）。</summary>
    public IReadOnlyList<string> SelectedCollections =>
        Collections.Where(c => c.IsSelected).Select(c => c.Name).ToList();

    /// <summary>当前 Tab 上用于添加集合的临时输入文本。</summary>
    private string _newCollectionName = string.Empty;

    public string NewCollectionName
    {
        get => _newCollectionName;
        set => SetProperty(ref _newCollectionName, value);
    }

    /// <summary>是否至少有一个集合被勾选。</summary>
    public bool HasSelectedCollection => SelectedCollections.Count > 0;

    /// <summary>输入框文本。</summary>
    public string InputText
    {
        get => _inputText;
        set
        {
            if (SetProperty(ref _inputText, value))
            {
                OnPropertyChanged(nameof(HasInput));
                SendCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>待随本次对话发送的附件列表（文档、图片、代码等）。</summary>
    public ObservableCollection<AttachmentItem> PendingAttachments { get; } = [];

    /// <summary>是否有待发送附件。</summary>
    public bool HasPendingAttachments => PendingAttachments.Count > 0;

    /// <summary>是否有输入内容（包含文本或待发附件）。</summary>
    public bool HasInput => !string.IsNullOrWhiteSpace(InputText) || HasPendingAttachments;

    /// <summary>添加附件（打开系统文件选择器）。</summary>
    [RelayCommand]
    private void AddAttachment()
    {
        var dlg = new Microsoft.Win32.OpenFileDialog
        {
            Title = "选择要导入到对话的文档或图片",
            Multiselect = true,
            Filter = "所有支持格式|*.pdf;*.docx;*.doc;*.xlsx;*.xls;*.pptx;*.ppt;*.md;*.markdown;*.html;*.htm;*.txt;*.py;*.cs;*.js;*.ts;*.java;*.go;*.rs;*.cpp;*.c;*.h;*.json;*.yaml;*.yml;*.sql;*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.tiff|" +
                     "文档与表格 (*.pdf,*.docx,*.xlsx,*.pptx,*.md)|*.pdf;*.docx;*.doc;*.xlsx;*.xls;*.pptx;*.ppt;*.md;*.markdown;*.html;*.htm;*.txt|" +
                     "图片与扫描件 (*.png,*.jpg,*.jpeg,*.bmp,*.webp)|*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.tiff|" +
                     "所有文件 (*.*)|*.*"
        };
        if (dlg.ShowDialog() == true)
        {
            AddAttachmentPaths(dlg.FileNames);
        }
    }

    /// <summary>移除待发送附件。</summary>
    [RelayCommand]
    private void RemoveAttachment(AttachmentItem? item)
    {
        if (item != null && PendingAttachments.Remove(item))
        {
            OnPropertyChanged(nameof(HasPendingAttachments));
            OnPropertyChanged(nameof(HasInput));
            SendCommand.NotifyCanExecuteChanged();
        }
    }

    /// <summary>清空待发送附件。</summary>
    [RelayCommand]
    private void ClearAttachments()
    {
        PendingAttachments.Clear();
        OnPropertyChanged(nameof(HasPendingAttachments));
        OnPropertyChanged(nameof(HasInput));
        SendCommand.NotifyCanExecuteChanged();
    }

    /// <summary>批量添加文件路径到待发送附件中（支持拖拽与多选）。</summary>
    public void AddAttachmentPaths(IEnumerable<string> paths)
    {
        foreach (var path in paths)
        {
            if (string.IsNullOrWhiteSpace(path) || !System.IO.File.Exists(path)) continue;
            if (PendingAttachments.Any(a => string.Equals(a.FullPath, path, StringComparison.OrdinalIgnoreCase))) continue;

            var fi = new System.IO.FileInfo(path);
            var ext = fi.Extension.ToLowerInvariant();
            var icon = ext switch
            {
                ".png" or ".jpg" or ".jpeg" or ".bmp" or ".webp" or ".tiff" => "🖼️",
                ".pdf" => "📕",
                ".docx" or ".doc" => "📘",
                ".xlsx" or ".xls" or ".csv" => "📊",
                ".pptx" or ".ppt" => "📙",
                ".md" or ".markdown" or ".txt" => "📝",
                ".py" or ".cs" or ".js" or ".ts" or ".java" or ".go" or ".rs" or ".cpp" or ".c" or ".h" or ".json" or ".sql" => "💻",
                _ => "📎"
            };
            var sizeText = fi.Length < 1024 * 1024 
                ? $"{fi.Length / 1024.0:F1} KB" 
                : $"{fi.Length / (1024.0 * 1024.0):F1} MB";

            PendingAttachments.Add(new AttachmentItem(fi.FullName, fi.Name, icon, sizeText));
        }
        OnPropertyChanged(nameof(HasPendingAttachments));
        OnPropertyChanged(nameof(HasInput));
        SendCommand.NotifyCanExecuteChanged();
    }

    /// <summary>是否正在请求中。</summary>
    public bool IsBusy
    {
        get => _isBusy;
        set
        {
            if (SetProperty(ref _isBusy, value))
            {
                OnPropertyChanged(nameof(ShowEmptyGuide));
                OnPropertyChanged(nameof(ShowStop));
                SendCommand.NotifyCanExecuteChanged();
                StopCommand.NotifyCanExecuteChanged();
                RegenerateCommand.NotifyCanExecuteChanged();
                UpdateMessageFlags();
            }
        }
    }

    /// <summary>状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>从后端拉取已有知识库集合，并合并用户手动添加的。</summary>
    public IAsyncRelayCommand LoadCollectionsCommand { get; }

    /// <summary>添加一个新的知识库集合（仅本地勾选，发送时随请求带上）。</summary>
    public IAsyncRelayCommand<string?> AddCollectionCommand { get; }

    private void OnCollectionItemChanged(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(CollectionItem.IsSelected))
        {
            OnPropertyChanged(nameof(HasSelectedCollection));
            SendCommand.NotifyCanExecuteChanged();
        }
    }

    private async Task LoadCollectionsAsync()
    {
        try
        {
            var stats = await _apiService.GetStatsAsync();
            var names = stats.Collections.Keys.ToHashSet();

            // 保留用户已手动添加、但后端尚不存在的集合
            foreach (var existing in Collections.ToList())
            {
                if (!names.Contains(existing.Name))
                {
                    names.Add(existing.Name);
                }
            }

            // 记录当前勾选状态，刷新后恢复，避免自动刷新丢失用户选择
            var selectedNames = Collections
                .Where(c => c.IsSelected)
                .Select(c => c.Name)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);

            Collections.Clear();
            var list = names.ToList();
            list.Sort(StringComparer.OrdinalIgnoreCase);
            foreach (var name in list)
            {
                // 已有勾选则恢复；无任何勾选时（首次加载）默认选 default
                var isSelected = selectedNames.Contains(name)
                    || (selectedNames.Count == 0 && string.Equals(name, "default", StringComparison.OrdinalIgnoreCase));
                Collections.Add(new CollectionItem { Name = name, IsSelected = isSelected });
            }

            OnPropertyChanged(nameof(HasSelectedCollection));
            SendCommand.NotifyCanExecuteChanged();
            DebugLog.Info($"集合列表加载完成: {list.Count} 个 [{string.Join(", ", list)}]", "Chat");
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"加载集合列表失败: {ex.Message}", "Chat");
            // 失败时至少保证有一个 default 可选
            if (Collections.Count == 0)
            {
                Collections.Add(new CollectionItem { Name = "default", IsSelected = true });
            }
        }
    }

    private async Task AddCollectionAsync(string? name)
    {
        var trimmed = (name ?? NewCollectionName).Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return;
        }

        try
        {
            // 真正在后端创建一个新的空知识库集合，并刷新列表
            var stats = await _apiService.CreateCollectionAsync(trimmed);
            var names = stats.Collections.Keys.ToHashSet();

            // 保留用户已手动添加但后端尚不存在的集合（理论上创建后已存在）
            foreach (var existing in Collections.ToList())
            {
                if (!names.Contains(existing.Name))
                {
                    names.Add(existing.Name);
                }
            }

            Collections.Clear();
            var list = names.ToList();
            list.Sort(StringComparer.OrdinalIgnoreCase);
            foreach (var n in list)
            {
                var isSelected = string.Equals(n, trimmed, StringComparison.OrdinalIgnoreCase)
                                 || string.Equals(n, "default", StringComparison.OrdinalIgnoreCase);
                Collections.Add(new CollectionItem { Name = n, IsSelected = isSelected });
            }

            StatusMessage = $"已创建知识库：{trimmed}";
            DebugLog.Info($"创建知识库集合成功: {trimmed}", "Chat");
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"创建知识库集合失败: {ex.Message}", "Chat");
            StatusMessage = $"创建失败：{ex.Message}";
            // 失败时不阻塞用户：仍把该集合加入本地列表并勾选，便于重试或离线使用
            if (!Collections.Any(c => string.Equals(c.Name, trimmed, StringComparison.OrdinalIgnoreCase)))
            {
                Collections.Add(new CollectionItem { Name = trimmed, IsSelected = true });
            }
        }

        NewCollectionName = string.Empty;
        OnPropertyChanged(nameof(HasSelectedCollection));
        SendCommand.NotifyCanExecuteChanged();
    }

    /// <summary>对话消息列表。</summary>
    public ObservableCollection<ChatMessage> Messages { get; } = [];

    /// <summary>是否有消息。</summary>
    public bool HasMessages => Messages.Count > 0;

    /// <summary>无消息时显示空态。</summary>
    public bool ShowEmptyGuide => !IsBusy && Messages.Count == 0;

    /// <summary>空态引导文案。</summary>
    public string EmptyGuideText =>
        "开始与知识库对话。\n\n"
        + "DocMind 会检索已导入的文档，\n"
        + "结合多轮上下文生成带来源标注的回答。\n\n"
        + "还没导入文档？先到【导入】页添加文件。\n\n"
        + "💡 需要先配置 LLM：到【设置 → 大模型对话】\n"
        + "  选择提供商（OpenAI 兼容 / Claude / Gemini / Ollama）\n"
        + "  填写 API Key 后点「测试连接」验证";

    private bool CanSend => !IsBusy && HasInput;

    /// <summary>是否可重新生成（非生成中，且最后一条是 assistant 消息）。</summary>
    private bool CanRegenerate => !IsBusy && Messages.Count > 0 && Messages[^1].Role == "assistant";

    /// <summary>是否可停止（生成中）。</summary>
    private bool CanStop => IsBusy;

    /// <summary>停止命令是否可见（生成中显示停止按钮）。</summary>
    public bool ShowStop => IsBusy;

    /// <summary>发送消息（流式）。</summary>
    [RelayCommand(CanExecute = nameof(CanSend))]
    private async Task SendAsync()
    {
        var query = InputText.Trim();
        if (string.IsNullOrWhiteSpace(query))
        {
            if (HasPendingAttachments)
            {
                query = "请分析并总结我上传的附件内容。";
            }
            else
            {
                return;
            }
        }

        await SendCoreAsync(query, addUserMessage: true);
    }

    /// <summary>点击推荐行动胶囊：将建议填入并直接发送问答。</summary>
    [RelayCommand]
    private async Task ExecuteActionAsync(string? action)
    {
        if (string.IsNullOrWhiteSpace(action) || IsBusy)
            return;

        var query = action.Trim();
        const string prefix = "👉";
        if (query.StartsWith(prefix, StringComparison.Ordinal))
        {
            query = query[prefix.Length..].Trim();
        }

        InputText = query;
        await SendAsync();
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

    /// <summary>打开沉淀入库微调弹窗（支持整条消息或划词片段）。</summary>
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

        // 提取首行作为推荐标题
        var firstLine = contentToIngest.Split('\n', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? contentToIngest;
        firstLine = System.Text.RegularExpressions.Regex.Replace(firstLine, @"^[#\s\-*📌💡]+", "").Trim();
        var initialTitle = firstLine.Length > 28 ? firstLine[..28] + "…" : firstLine;
        if (string.IsNullOrWhiteSpace(initialTitle))
        {
            initialTitle = $"知识探讨沉淀 ({DateTime.Now:MM-dd HH:mm})";
        }

        IngestDialogTitle = initialTitle;
        IngestDialogContent = contentToIngest;
        IngestDialogTags = "探讨笔记, 精炼结论";

        var targetCol = SelectedCollections.FirstOrDefault() ?? "default";
        IngestDialogCollection = targetCol;

        IsIngestDialogOpen = true;
    }

    /// <summary>确认沉淀入库（带用户微调后的标题、集合与正文）。</summary>
    [RelayCommand]
    public async Task ConfirmIngestDialogAsync()
    {
        if (string.IsNullOrWhiteSpace(IngestDialogContent) || IsDialogIngesting)
            return;

        IsDialogIngesting = true;
        StatusMessage = "正在沉淀入库…";

        try
        {
            var text = IngestDialogContent.Trim();
            var title = string.IsNullOrWhiteSpace(IngestDialogTitle) ? "知识沉淀笔记" : IngestDialogTitle.Trim();
            var collection = string.IsNullOrWhiteSpace(IngestDialogCollection) ? "default" : IngestDialogCollection.Trim();

            // 若有标签，追加到正文顶部
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
            _notifications?.Success($"已成功沉淀至集合「{collection}」", "沉淀入库");
            StatusMessage = $"已沉淀入库：{title}";
            DebugLog.Info($"沉淀入库成功: title={title}, collection={collection}", "Chat");

            // 异步刷新知识库集合
            _ = LoadCollectionsAsync();
        }
        catch (Exception ex)
        {
            _notifications?.Error($"沉淀入库失败: {ex.Message}", "错误");
            StatusMessage = $"沉淀入库失败：{ex.Message}";
            DebugLog.Error($"沉淀入库异常: {ex}", "Chat");
        }
        finally
        {
            IsDialogIngesting = false;
        }
    }

    /// <summary>关闭沉淀入库弹窗。</summary>
    [RelayCommand]
    public void CloseIngestDialog()
    {
        IsIngestDialogOpen = false;
    }

    /// <summary>一键将回答沉淀为知识笔记入库（快捷入口，直接打开微调弹窗）。</summary>
    [RelayCommand]
    private void IngestMessage(ChatMessage? message)
    {
        OpenIngestDialog(message);
    }

    /// <summary>插入场景化快捷指令模板。</summary>
    [RelayCommand]
    private void InsertPromptTemplate(string? templateType)
    {
        var prefix = templateType switch
        {
            "ppt" => "请依托上述知识库资料，为我制作一份结构完整、逻辑清晰的专业汇报 PPT 演示文稿（包含封面、目录、核心论点与每页演讲备注）：\n",
            "report" => "请依托上述知识库资料，撰写一份结构严谨、包含方案论证与对比表格的技术研报/项目方案公文：\n",
            "lesson" => "请依托上述教材与资料，设计一份系统完整的课程教学大纲与分课时教案（含教学目标、重难点、教学过程、随堂测验）：\n",
            "matrix" => "请全方位提炼抽取相关方案与特性的参数指标，输出结构化的多维对比矩阵与评估表格：\n",
            "webpage" => "请为上述知识主题生成一个高颜值、自包含、带指标卡片与知识详情的交互式单文件 HTML 总结看板：\n",
            "summary" => "请将上述内容萃取提炼为核心结论与清晰的 Action Items 待办清单：\n",
            "table" => "请以结构化 Markdown 表格形式，全方位对比各方案的优缺点、适用场景与成本效益：\n",
            "polish" => "请将以下草稿按严谨专业的企业公文与技术汇报规范进行润色重构：\n",
            "pitfall" => "请对以下方案进行专家级把关评审，列出潜在风险点、性能隐患与避坑防范建议：\n",
            _ => string.Empty
        };

        // 联动自动切换对应创作人设
        var matchedPersonaId = templateType switch
        {
            "ppt" => "ppt",
            "report" => "doc",
            "lesson" => "lesson",
            "matrix" => "table",
            "webpage" => "web",
            _ => null
        };
        if (matchedPersonaId != null)
        {
            var p = AvailablePersonas.FirstOrDefault(x => x.Id == matchedPersonaId);
            if (p != null) SelectedPersona = p;
        }

        if (string.IsNullOrEmpty(InputText))
        {
            InputText = prefix;
        }
        else
        {
            InputText = prefix + InputText;
        }
    }

    /// <summary>重新生成最后一条回答：移除末尾 assistant 消息后重发最后一条用户问题（保留 chatId 多轮上下文）。</summary>
    [RelayCommand(CanExecute = nameof(CanRegenerate))]
    private async Task RegenerateAsync()
    {
        if (IsBusy)
            return;

        var lastUserIdx = -1;
        for (var i = Messages.Count - 1; i >= 0; i--)
        {
            if (Messages[i].Role == "user")
            {
                lastUserIdx = i;
                break;
            }
        }
        if (lastUserIdx < 0)
            return;

        var query = Messages[lastUserIdx].Content;
        // 移除该用户消息之后的所有消息（旧回答/错误占位）
        for (var i = Messages.Count - 1; i > lastUserIdx; i--)
        {
            Messages.RemoveAt(i);
        }
        DebugLog.Info($"重新生成: 移除 {Messages.Count - lastUserIdx - 1} 条旧回答后重发", "Chat");
        await SendCoreAsync(query, addUserMessage: false);
    }

    /// <summary>发送核心：添加用户消息（可选）+ 流式请求 + 终帧回写。Send 与 Regenerate 共用。</summary>
    private async Task SendCoreAsync(string query, bool addUserMessage)
    {
        // 提取附件列表
        var attachments = PendingAttachments.Select(a => a.FullPath).ToList();
        var attachLabels = PendingAttachments.Select(a => $"{a.Icon} {a.FileName}").ToList();
        PendingAttachments.Clear();
        OnPropertyChanged(nameof(HasPendingAttachments));
        OnPropertyChanged(nameof(HasInput));
        SendCommand.NotifyCanExecuteChanged();

        // 添加用户消息
        if (addUserMessage)
        {
            var userDisplay = query;
            if (attachLabels.Count > 0)
            {
                userDisplay = $"[📎 附件: {string.Join(", ", attachLabels)}]\n{query}";
            }
            Messages.Add(new ChatMessage { Role = "user", Content = userDisplay });
        }

        // 添加流式占位（先空内容，逐 token 追加）
        var assistantMsg = new ChatMessage { Role = "assistant", Content = "", IsLoading = true, IsWaitingForFirstToken = true };
        Messages.Add(assistantMsg);
        
        // 发送开始前，清空输入框
        InputText = string.Empty;

        IsBusy = true;
        ShowStopChanged();
        StatusMessage = "对话中…";
        var sw = System.Diagnostics.Stopwatch.StartNew();

        _cts = new CancellationTokenSource();
        // 流式排查统计：token 帧数 + 首 token 延迟（TTFT）
        var tokenCount = 0;
        long firstTokenMs = -1;
        try
        {
            var selected = SelectedCollections;
            DebugLog.Info(
                $"发送消息: query='{(query.Length > 100 ? query[..100] + "…" : query)}' " +
                $"collections=[{string.Join(",", selected)}] chatId='{_chatId ?? "-"}' model='{(SelectedModel == DefaultModelLabel ? "-" : SelectedModel)}' persona='{SelectedPersona?.Id ?? "-"}' msgCount={Messages.Count} attachCount={attachments.Count}",
                "Chat");
            ChatStreamResult? final = null;
            await _apiService.ChatStreamAsync(
                new ChatRequest
                {
                    Query = query,
                    Collections = selected.Count > 0 ? selected : null,
                    // TopK 不传（null）：由后端按设置页的 rag_top_k 决定，
                    // 避免对话页硬编码 5 覆盖用户配置（此前设置页改引用数无效）
                    TopK = null,
                    ChatId = _chatId,
                    // 对话页快速切换模型：默认项不带（用设置页配置），选了具体模型则按请求覆盖
                    Model = SelectedModel == DefaultModelLabel ? null : SelectedModel,
                    Persona = SelectedPersona?.Id,
                    Attachments = attachments.Count > 0 ? attachments : null,
                },
                onToken: token =>
                {
                    if (tokenCount == 0)
                    {
                        firstTokenMs = sw.ElapsedMilliseconds;
                    }
                    tokenCount++;

                    void ApplyToken()
                    {
                        if (assistantMsg.IsWaitingForFirstToken)
                        {
                            assistantMsg.IsWaitingForFirstToken = false;
                            assistantMsg.Content = token;
                        }
                        else
                        {
                            assistantMsg.Content += token;
                        }
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyToken);
                    }
                    else
                    {
                        ApplyToken();
                    }
                },
                onDone: result =>
                {
                    final = result;
                    void ApplyDone()
                    {
                        // 终帧后强制重新解析 Markdown,确保最终渲染完整(不受流式节流影响)
                        assistantMsg.ForceRefreshRender();
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyDone);
                    }
                    else
                    {
                        ApplyDone();
                    }
                },
                ct: _cts.Token);

            sw.Stop();
            DebugLog.Info(
                $"流式统计: tokens={tokenCount} 首token={(firstTokenMs >= 0 ? $"{firstTokenMs}ms" : "未收到")} " +
                $"收到done帧={(final is not null ? "是" : "否")} 总耗时{sw.ElapsedMilliseconds}ms",
                "Chat");

            // 终帧：回写多轮 chat_id + 来源 + 元数据（流式多轮不中断的关键）
            if (final is not null)
            {
                var isNewChat = _chatId is null && !string.IsNullOrEmpty(final.ChatId);
                _chatId = final.ChatId ?? _chatId;
                assistantMsg.Sources = final.Sources;
                assistantMsg.Model = final.Model;
                assistantMsg.Provider = final.Provider;
                assistantMsg.ElapsedMs = final.ElapsedMs;

                StatusMessage = $"模型: {final.Model} ({final.Provider}) · 引用 {final.TotalChunks} 块 · 耗时 {final.ElapsedMs}ms";
                DebugLog.Info($"对话完成(流式): elapsed={final.ElapsedMs}ms model={final.Model} chunks={final.TotalChunks} sources={final.Sources.Count} chatId='{final.ChatId}'", "Chat");

                // 新会话首条回答完成 → 刷新会话列表（标题/条数已生成），选中当前会话
                if (isNewChat)
                {
                    _ = LoadSessionsAsync(selectChatId: _chatId);
                }
            }
            else
            {
                DebugLog.Warn("未收到 done 终帧：多轮 chat_id 未更新、来源/模型元数据缺失（后端可能异常中断流）", "Chat");
                StatusMessage = $"对话完成 · 耗时 {sw.ElapsedMilliseconds}ms";
            }

            assistantMsg.IsLoading = false;
            if (string.IsNullOrEmpty(assistantMsg.Content))
            {
                assistantMsg.Content = "（无内容返回）";
            }
            UpdateMessageFlags();
        }
        catch (OperationCanceledException)
        {
            sw.Stop();
            StatusMessage = "已停止生成";
            DebugLog.Info("对话已停止", "Chat");
            if (string.IsNullOrEmpty(assistantMsg.Content))
            {
                assistantMsg.Content = "（已停止生成）";
            }
        }
        catch (ApiException ex)
        {
            sw.Stop();
            var hint = LlmConfigHint(ex.Message);
            StatusMessage = hint is null
                ? $"API 错误：{ex.Message}"
                : $"API 错误：{ex.Message}（请到设置页检查 LLM 配置）";
            DebugLog.Error($"对话 API 错误: code={ex.Code} message={ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.IsWaitingForFirstToken = false;
            assistantMsg.Content = hint is null
                ? $"❌ API 错误：{ex.Message}"
                : $"❌ {hint}";
            // 发送失败时，将原文回填到输入框，避免草稿丢失
            InputText = query;
        }
        catch (BackendConnectionException ex)
        {
            sw.Stop();
            StatusMessage = "无法连接到后端";
            DebugLog.Error($"对话连接失败: {ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.IsWaitingForFirstToken = false;
            assistantMsg.Content = $"❌ 无法连接到后端服务: {ex.Message}\n\n💡 请检查:\n1. 端口是否被占用\n2. 可尝试在【设置】中修改「后端连接地址」端口并保存";
            InputText = query;
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"对话未知异常: {ex.GetType().Name}: {ex.Message}", "Chat", ex);
            assistantMsg.IsLoading = false;
            assistantMsg.IsWaitingForFirstToken = false;
            assistantMsg.Content = $"❌ 错误：{ex.Message}";
            InputText = query;
        }
        finally
        {
            _cts?.Dispose();
            _cts = null;
            IsBusy = false;
            ShowStopChanged();
            OnPropertyChanged(nameof(HasMessages));
        }
    }

    private CancellationTokenSource? _cts;

    /// <summary>LLM 鉴权/未配置类错误 → 返回引导到设置页的提示文案；其余错误返回 null。
    /// 后端把 LLM 调用错误包在 RAG_ERROR 的消息文本里（如「API Key 无效 (HTTP 401)」「未选择 LLM 提供商」），
    /// 这里按关键词启发式分类，给用户可操作的下一步而不是裸错误。</summary>
    private static string? LlmConfigHint(string? message)
    {
        if (string.IsNullOrEmpty(message))
        {
            return null;
        }
        var m = message.ToLowerInvariant();
        var isAuth = m.Contains("401") || m.Contains("403") || m.Contains("unauthorized")
            || m.Contains("api key") || m.Contains("apikey") || m.Contains("鉴权") || m.Contains("密钥");
        var isNotConfigured = m.Contains("未配置") || m.Contains("未设置") || m.Contains("未选择")
            || m.Contains("llm_provider") || m.Contains("provider=none") || m.Contains("no provider");
        return (isAuth, isNotConfigured) switch
        {
            (true, _) => "API Key 无效或未配置：请到【设置 → 大模型对话】检查 API Key 后点「保存」",
            (_, true) => "尚未配置 LLM：请到【设置 → 大模型对话】选择提供商、填写 API Key，并点「测试连接」验证",
            _ => null,
        };
    }

    /// <summary>停止生成。</summary>
    [RelayCommand(CanExecute = nameof(CanStop))]
    private void Stop()
    {
        DebugLog.Info("请求停止生成（用户点击停止按钮）", "Chat");
        try
        {
            _cts?.Cancel();
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"取消生成时异常: {ex.Message}", "Chat");
        }
    }

    private void ShowStopChanged()
    {
        OnPropertyChanged(nameof(ShowStop));
        StopCommand.NotifyCanExecuteChanged();
    }

    /// <summary>清空当前对话视图（不删除后端会话记录）。</summary>
    [RelayCommand]
    private void Clear()
    {
        DebugLog.Info($"清空对话: messages={Messages.Count} chatId='{_chatId ?? "-"}'", "Chat");
        // 进行中则先停止
        try { _cts?.Cancel(); } catch { /* ignore */ }
        Messages.Clear();
        _chatId = null;
        _selectedSession = null;
        InputText = string.Empty;
        OnPropertyChanged(nameof(SelectedSession));
        StatusMessage = "就绪";
        OnPropertyChanged(nameof(HasMessages));
    }

    /// <summary>开始新会话（清空视图并取消会话选中）。</summary>
    [RelayCommand]
    private void NewChat() => Clear();

    /// <summary>删除历史会话（后端 SQLite + 内存）；删除当前会话则切到新会话。</summary>
    [RelayCommand]
    private async Task DeleteSessionAsync(ChatSessionItem? session)
    {
        session ??= SelectedSession;
        if (session is null || _isLoadingSessions)
            return;

        try
        {
            await _apiService.DeleteChatAsync(session.ChatId);
            Sessions.Remove(session);
            DebugLog.Info($"已删除会话: {session.ChatId} '{session.Title}'", "Chat");

            if (session.ChatId == _chatId || session.ChatId == SelectedSession?.ChatId)
            {
                Clear();
            }
            StatusMessage = $"已删除会话：{session.Title}";
        }
        catch (Exception ex)
        {
            StatusMessage = $"删除会话失败：{ex.Message}";
            DebugLog.Warn($"删除会话失败: {session.ChatId}: {ex.Message}", "Chat");
        }
    }

    /// <summary>撤回用户消息并回填到输入框（可传入特定消息，默认撤回最后一条用户消息）。</summary>
    [RelayCommand]
    private void Withdraw(ChatMessage? message = null)
    {
        if (IsBusy || Messages.Count == 0)
            return;

        int userIdx = -1;
        if (message != null)
        {
            userIdx = Messages.IndexOf(message);
        }
        else
        {
            for (var i = Messages.Count - 1; i >= 0; i--)
            {
                if (Messages[i].Role == "user")
                {
                    userIdx = i;
                    break;
                }
            }
        }

        if (userIdx < 0 || userIdx >= Messages.Count || Messages[userIdx].Role != "user")
            return;

        var content = Messages[userIdx].Content;
        // 移除该用户消息及其后的所有消息（如对应的 AI 回答）
        while (Messages.Count > userIdx)
        {
            Messages.RemoveAt(Messages.Count - 1);
        }

        InputText = content;
        StatusMessage = "已撤回消息并回填至输入框";
        DebugLog.Info($"已撤回用户消息: '{content}'", "Chat");
        UpdateMessageFlags();
    }

    /// <summary>维护消息级标志（复制由 CanCopy 自算；重新生成仅最后一条 assistant 显示；撤回仅最后一条 user 消息在非忙碌时显示）。</summary>
    private void UpdateMessageFlags()
    {
        int lastUserIdx = -1;
        for (var i = Messages.Count - 1; i >= 0; i--)
        {
            if (Messages[i].Role == "user")
            {
                lastUserIdx = i;
                break;
            }
        }

        for (var i = 0; i < Messages.Count; i++)
        {
            var m = Messages[i];
            m.ShowRegenerate = i == Messages.Count - 1 && m.Role == "assistant" && !IsBusy;
            m.ShowWithdraw = i == lastUserIdx && !IsBusy;
        }
    }

    /// <summary>拉取历史会话列表（后端不可达时静默）。selectChatId 非空时选中该会话。</summary>
    private async Task LoadSessionsAsync(string? selectChatId = null)
    {
        try
        {
            var list = await _apiService.ListChatsAsync(limit: 50);
            _isLoadingSessions = true;
            try
            {
                Sessions.Clear();
                foreach (var s in list.Chats)
                {
                    Sessions.Add(new ChatSessionItem
                    {
                        ChatId = s.ChatId,
                        Title = s.Title,
                        MessageCount = s.MessageCount,
                        UpdatedAt = s.UpdatedAt,
                    });
                }

                // 选中目标会话：优先 selectChatId（新完成的首答），否则跟随当前 chatId
                var targetId = selectChatId ?? _chatId;
                SelectedSession = targetId is null
                    ? null
                    : Sessions.FirstOrDefault(s => s.ChatId == targetId);
            }
            finally
            {
                _isLoadingSessions = false;
            }
            DebugLog.Info($"会话列表加载完成: {Sessions.Count} 个", "Chat");
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"加载会话列表失败: {ex.Message}", "Chat");
        }
    }

    /// <summary>选中历史会话 → 拉取全部消息载入视图，续聊沿用同一 chatId（后端从 DB 恢复上下文）。</summary>
    private async Task LoadSessionMessagesAsync(ChatSessionItem session)
    {
        try
        {
            var detail = await _apiService.GetChatAsync(session.ChatId);
            Messages.Clear();
            foreach (var m in detail.Messages)
            {
                Messages.Add(new ChatMessage
                {
                    Role = m.Role,
                    Content = m.Content,
                    Sources = m.Sources,
                });
            }
            _chatId = session.ChatId;
            StatusMessage = $"已载入会话：{session.Title}（{detail.Messages.Count} 条消息，可继续追问）";
            DebugLog.Info($"载入历史会话: {session.ChatId} messages={detail.Messages.Count}", "Chat");
        }
        catch (Exception ex)
        {
            StatusMessage = $"载入会话失败：{ex.Message}";
            DebugLog.Error($"载入历史会话失败: {session.ChatId}: {ex.Message}", "Chat", ex);
        }
    }

    private SourceRef? _selectedSource;
    private bool _isSourceDrawerOpen;
    private ArtifactItem? _selectedArtifact;
    private int _currentSlideIndex;
    private bool _isArtifactMode;

    /// <summary>当前选中的创作物交付物（供右侧创作画布展示）。</summary>
    public ArtifactItem? SelectedArtifact
    {
        get => _selectedArtifact;
        set
        {
            if (SetProperty(ref _selectedArtifact, value))
            {
                OnPropertyChanged(nameof(HasSelectedArtifact));
                OnPropertyChanged(nameof(SelectedArtifactTitle));
                OnPropertyChanged(nameof(SelectedSlide));
                OnPropertyChanged(nameof(SlideCountText));
                OnPropertyChanged(nameof(CanPrevSlide));
                OnPropertyChanged(nameof(CanNextSlide));
                OnPropertyChanged(nameof(IsPptArtifact));
                OnPropertyChanged(nameof(IsDocArtifact));
                OnPropertyChanged(nameof(IsExcelArtifact));
                OnPropertyChanged(nameof(IsHtmlArtifact));
            }
        }
    }

    public bool HasSelectedArtifact => SelectedArtifact != null;
    public string SelectedArtifactTitle => SelectedArtifact?.Title ?? "创作交付物";
    public bool IsPptArtifact => SelectedArtifact?.IsPpt == true;
    public bool IsDocArtifact => SelectedArtifact?.IsDoc == true;
    public bool IsExcelArtifact => SelectedArtifact?.IsExcel == true;
    public bool IsHtmlArtifact => SelectedArtifact?.IsHtml == true;

    /// <summary>当前正在预览的幻灯片页索引（从 0 开始）。</summary>
    public int CurrentSlideIndex
    {
        get => _currentSlideIndex;
        set
        {
            if (SetProperty(ref _currentSlideIndex, value))
            {
                OnPropertyChanged(nameof(SelectedSlide));
                OnPropertyChanged(nameof(SlideCountText));
                OnPropertyChanged(nameof(CanPrevSlide));
                OnPropertyChanged(nameof(CanNextSlide));
            }
        }
    }

    /// <summary>当前选中的幻灯片页。</summary>
    public SlideItem? SelectedSlide =>
        SelectedArtifact?.Slides is { Count: > 0 } slides && CurrentSlideIndex >= 0 && CurrentSlideIndex < slides.Count
            ? slides[CurrentSlideIndex]
            : null;

    /// <summary>幻灯片页码文案（如 "1 / 8"）。</summary>
    public string SlideCountText => SelectedArtifact?.Slides is { Count: > 0 } slides
        ? $"{CurrentSlideIndex + 1} / {slides.Count}"
        : "0 / 0";

    public bool CanPrevSlide => CurrentSlideIndex > 0;
    public bool CanNextSlide => SelectedArtifact?.Slides is { Count: > 0 } slides && CurrentSlideIndex < slides.Count - 1;

    /// <summary>抽屉是否处于创作物工作台模式（false 为原著切片模式）。</summary>
    public bool IsArtifactMode
    {
        get => _isArtifactMode;
        set => SetProperty(ref _isArtifactMode, value);
    }

    /// <summary>当前选中的引用来源（供右侧协同抽屉预览）。</summary>
    public SourceRef? SelectedSource
    {
        get => _selectedSource;
        set
        {
            if (SetProperty(ref _selectedSource, value))
            {
                OnPropertyChanged(nameof(HasSelectedSource));
                OnPropertyChanged(nameof(SelectedSourceTitle));
                OnPropertyChanged(nameof(SelectedSourceSnippet));
            }
        }
    }

    public bool HasSelectedSource => SelectedSource != null;
    public string SelectedSourceTitle => SelectedSource?.DisplayTitle ?? "(未命名来源)";
    public string SelectedSourceSnippet => !string.IsNullOrWhiteSpace(SelectedSource?.Snippet)
        ? SelectedSource.Snippet
        : "（该切片暂无全文预览或来自早期版本会话，可通过下方动作查看原文）";

    /// <summary>协同来源预览抽屉是否展开。</summary>
    public bool IsSourceDrawerOpen
    {
        get => _isSourceDrawerOpen;
        set => SetProperty(ref _isSourceDrawerOpen, value);
    }

    /// <summary>5 套企业级演示文稿主题配色。</summary>
    public IReadOnlyList<PptThemeOption> AvailableThemes { get; } = new List<PptThemeOption>
    {
        new("tech_blue", "🔷 科技商务蓝", "🔷", "深邃稳健，架构汇报首选", "#0F4C81", "#F6F8FC"),
        new("emerald_green", "🌿 清新自然绿", "🌿", "战略规划、ESG 与教育", "#1B4D3E", "#F4F7F5"),
        new("modern_purple", "🟣 AI 智能紫", "🟣", "前沿创新、未来科技", "#4A148C", "#F7F5FD"),
        new("warm_orange", "🔶 活力暖橙红", "🔶", "商业营销与成果战报", "#B73225", "#FEF8F6"),
        new("dark_elegant", "⬛ 极简暗黑风", "⬛", "沉浸发布会、极客科技", "#60A5FA", "#181A20"),
    };

    private PptThemeOption? _selectedTheme;

    /// <summary>当前选中的 PPT 主题配色。</summary>
    public PptThemeOption SelectedTheme
    {
        get => _selectedTheme ?? AvailableThemes[0];
        set => SetProperty(ref _selectedTheme, value ?? AvailableThemes[0]);
    }

    /// <summary>打开创作物画布抽屉。</summary>
    [RelayCommand]
    public void OpenArtifact(object? param)
    {
        ArtifactItem? item = null;
        if (param is ArtifactItem ai) item = ai;
        else if (param is ChatMessage msg && msg.Artifact != null) item = msg.Artifact;

        if (item != null)
        {
            SelectedArtifact = item;
            CurrentSlideIndex = 0;
            IsArtifactMode = true;
            IsSourceDrawerOpen = true;

            // 匹配并同步主题
            if (!string.IsNullOrWhiteSpace(item.Theme))
            {
                var matchedTheme = AvailableThemes.FirstOrDefault(t => string.Equals(t.Id, item.Theme, StringComparison.OrdinalIgnoreCase));
                if (matchedTheme != null) SelectedTheme = matchedTheme;
            }

            StatusMessage = $"展开创作物画布：{item.Title} ({item.Type.ToUpperInvariant()})";
            DebugLog.Info($"展开创作物画布: title={item.Title} type={item.Type} slides={item.SlideCount}", "Chat");
        }
    }

    /// <summary>幻灯片上一页。</summary>
    [RelayCommand]
    private void PrevSlide()
    {
        if (CanPrevSlide)
        {
            CurrentSlideIndex--;
        }
    }

    /// <summary>幻灯片下一页。</summary>
    [RelayCommand]
    private void NextSlide()
    {
        if (CanNextSlide)
        {
            CurrentSlideIndex++;
        }
    }

    /// <summary>一键将创作物导出为本地物理文件（PPTX/DOCX/XLSX/HTML）。</summary>
    [RelayCommand]
    public async Task ExportArtifactFileAsync(string? targetFormat = null)
    {
        var artifact = SelectedArtifact;
        if (artifact == null || string.IsNullOrWhiteSpace(artifact.RawContent))
        {
            _notifications?.Warning("当前没有可导出的创作物内容");
            return;
        }

        var fmt = targetFormat ?? artifact.Type;
        StatusMessage = $"正在编译导出 {fmt.ToUpperInvariant()} 物理文件（主题: {SelectedTheme.DisplayName}）…";

        try
        {
            var req = new CreativeExportRequest
            {
                Content = artifact.RawContent,
                Format = fmt,
                Title = artifact.Title,
                Theme = SelectedTheme.Id,
            };

            var res = await _apiService.ExportCreativeArtifactAsync(req);
            if (res.Ok && !string.IsNullOrWhiteSpace(res.FilePath))
            {
                StatusMessage = $"已导出文件：{res.FileName}";
                _notifications?.Success($"已成功导出至：{res.FileName}\n路径：{res.FilePath}", "创作导出成功");
                DebugLog.Info($"导出创作物物理文件成功: path={res.FilePath} size={res.FileSizeBytes}", "Chat");

                // 尝试在 Windows 资源管理器中高亮选中生成的文件
                try
                {
                    if (System.IO.File.Exists(res.FilePath))
                    {
                        Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{res.FilePath}\"") { UseShellExecute = true });
                    }
                }
                catch (Exception ex)
                {
                    DebugLog.Warn($"在资源管理器中定位导出文件异常: {ex.Message}", "Chat");
                }
            }
            else
            {
                StatusMessage = $"导出失败：{res.Error ?? "未知错误"}";
                _notifications?.Error($"导出失败: {res.Error}");
                DebugLog.Error($"导出交付物失败: {res.Error}", "Chat");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"导出异常：{ex.Message}";
            DebugLog.Error($"导出交付物异常: {ex.Message}", "Chat", ex);
            _notifications?.Error($"导出异常: {ex.Message}");
        }
    }

    private PptInspectionReportDto? _inspectionReport;
    private bool _isInspectionReportOpen;

    /// <summary>当前 PPT 效果自检与质量诊断报告。</summary>
    public PptInspectionReportDto? InspectionReport
    {
        get => _inspectionReport;
        set
        {
            if (SetProperty(ref _inspectionReport, value))
            {
                OnPropertyChanged(nameof(HasInspectionReport));
            }
        }
    }

    public bool HasInspectionReport => InspectionReport != null;

    /// <summary>自检报告抽屉是否展开。</summary>
    public bool IsInspectionReportOpen
    {
        get => _isInspectionReportOpen;
        set => SetProperty(ref _isInspectionReportOpen, value);
    }

    /// <summary>对当前创作物进行效果自检体检诊断。</summary>
    [RelayCommand]
    public async Task InspectPptAsync()
    {
        var artifact = SelectedArtifact;
        if (artifact == null || string.IsNullOrWhiteSpace(artifact.RawContent))
        {
            _notifications?.Warning("当前没有可自检的 PPT 内容");
            return;
        }

        StatusMessage = "正在对演示文稿进行全方位效果自检与体检评分…";

        try
        {
            var report = await _apiService.InspectCreativeArtifactAsync(artifact.RawContent);
            InspectionReport = report;
            IsInspectionReportOpen = true;
            StatusMessage = $"PPT 自检完成：健康度得分 {report.Score} 分 ({report.Grade})";
            _notifications?.Info($"PPT 效果自检完成：健康得分 {report.Score} 分 ({report.Grade})\n{report.Summary}", "效果自检报告");
            DebugLog.Info($"PPT 效果自检完成: score={report.Score} grade={report.Grade} issues={report.Issues.Count}", "Chat");
        }
        catch (Exception ex)
        {
            StatusMessage = $"自检异常：{ex.Message}";
            _notifications?.Error($"效果自检失败: {ex.Message}");
            DebugLog.Error($"PPT 效果自检异常: {ex.Message}", "Chat", ex);
        }
    }

    /// <summary>关闭自检报告抽屉。</summary>
    [RelayCommand]
    public void CloseInspectionReport()
    {
        IsInspectionReportOpen = false;
    }

    private bool _isSlideShowOpen;
    private bool _isSpeakerNotesVisibleInSlideShow = true;

    /// <summary>是否开启客户端全屏沉浸放映预览。</summary>
    public bool IsSlideShowOpen
    {
        get => _isSlideShowOpen;
        set => SetProperty(ref _isSlideShowOpen, value);
    }

    /// <summary>全屏放映时是否显示演讲提词器抽屉。</summary>
    public bool IsSpeakerNotesVisibleInSlideShow
    {
        get => _isSpeakerNotesVisibleInSlideShow;
        set => SetProperty(ref _isSpeakerNotesVisibleInSlideShow, value);
    }

    /// <summary>开启客户端大屏沉浸放映预览。</summary>
    [RelayCommand]
    public void OpenSlideShow()
    {
        if (SelectedArtifact == null || !IsPptArtifact)
        {
            _notifications?.Warning("当前没有可放映的演示文稿");
            return;
        }
        IsSlideShowOpen = true;
        StatusMessage = "进入 PPT 大屏沉浸放映预览模式（按 Esc 退出，键盘左右键翻页）";
    }

    /// <summary>退出客户端全屏沉浸放映预览。</summary>
    [RelayCommand]
    public void CloseSlideShow()
    {
        IsSlideShowOpen = false;
        StatusMessage = "已退出大屏放映模式";
    }

    /// <summary>切换全屏放映时的提词小抄显示状态。</summary>
    [RelayCommand]
    public void ToggleSlideShowNotes()
    {
        IsSpeakerNotesVisibleInSlideShow = !IsSpeakerNotesVisibleInSlideShow;
    }

    /// <summary>在浏览器中一键秒开 16:9 交互式 SlideShow 网页放映预览。</summary>
    [RelayCommand]
    public async Task OpenWebPreviewAsync()
    {
        var artifact = SelectedArtifact;
        if (artifact == null || string.IsNullOrWhiteSpace(artifact.RawContent))
        {
            _notifications?.Warning("当前没有可预览的创作物内容");
            return;
        }

        StatusMessage = "正在编译 16:9 交互式 HTML5 幻灯片放映页面…";

        try
        {
            var req = new CreativeExportRequest
            {
                Content = artifact.RawContent,
                Format = "html",
                Title = artifact.Title,
                Theme = SelectedTheme.Id,
            };

            var res = await _apiService.ExportCreativeArtifactAsync(req);
            if (res.Ok && !string.IsNullOrWhiteSpace(res.FilePath) && System.IO.File.Exists(res.FilePath))
            {
                StatusMessage = "已在浏览器中启动 16:9 交互式放映预览";
                _notifications?.Success("已在浏览器中打开全屏交互式放映页面（支持键盘 ← → 翻页与 F 键全屏）", "网页放映启动");
                Process.Start(new ProcessStartInfo(res.FilePath) { UseShellExecute = true });
            }
            else
            {
                StatusMessage = $"网页预览生成失败: {res.Error ?? "未知错误"}";
                _notifications?.Error($"网页放映失败: {res.Error}");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"网页放映启动异常: {ex.Message}";
            _notifications?.Error($"启动异常: {ex.Message}");
            DebugLog.Error($"启动网页预览异常: {ex.Message}", "Chat", ex);
        }
    }

    /// <summary>点击引用来源：在右侧协同抽屉就地展开原著切片与元数据，不离开对话主界面。</summary>
    [RelayCommand]
    private void OpenSource(SourceRef? src)
    {
        if (src is null)
        {
            return;
        }
        IsArtifactMode = false;
        SelectedSource = src;
        IsSourceDrawerOpen = true;
        StatusMessage = $"查看切片出处：{src.DisplayTitle} (相似度: {src.Score:F2})";
        DebugLog.Info($"展开引用来源抽屉: index={src.Index} source={src.Source} page={src.Page}", "Chat");
    }

    /// <summary>关闭协同来源抽屉。</summary>
    [RelayCommand]
    private void CloseSourceDrawer()
    {
        IsSourceDrawerOpen = false;
    }

    /// <summary>复制当前抽屉中切片正文到剪贴板。</summary>
    [RelayCommand]
    private void CopySourceSnippet()
    {
        if (!string.IsNullOrWhiteSpace(SelectedSource?.Snippet))
        {
            try
            {
                Clipboard.SetText(SelectedSource.Snippet);
                _notifications?.Success("已复制切片原文到剪贴板");
                StatusMessage = "已复制切片原文到剪贴板";
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"复制到剪贴板失败: {ex.Message}", "Chat");
            }
        }
    }

    /// <summary>用户主动选择：在知识搜索页全文检索该文档（深钻次级动作）。</summary>
    [RelayCommand]
    private void SearchSourceInSearchPage()
    {
        if (SelectedSource is not null)
        {
            SourceSearchRequested?.Invoke(SelectedSource);
        }
    }

    /// <summary>一键体验官方示例文档库（针对新手/空状态）。</summary>
    [RelayCommand]
    private async Task IngestSampleKnowledgeAsync()
    {
        if (IsBusy) return;

        IsBusy = true;
        StatusMessage = "正在导入新手官方示例知识库...";
        try
        {
            var res = await _apiService.IngestSampleAsync("default");
            if (res.Ok)
            {
                await LoadCollectionsAsync();
                var def = Collections.FirstOrDefault(c => c.Name == "default");
                if (def != null && !def.IsSelected)
                {
                    def.IsSelected = true;
                }
                InputText = "请总结 DocMind 的核心能力与支持的文档格式";
                StatusMessage = $"示例知识库导入成功！(分块: {res.ChunkCount})，已为您准备好体验问题";
                _notifications?.Success("官方示例文档已导入！快来体验智能问答吧", "极速体验");
            }
            else
            {
                StatusMessage = $"导入失败: {res.Error ?? "未知错误"}";
                _notifications?.Error($"示例库导入失败: {res.Error}");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"导入异常: {ex.Message}";
            DebugLog.Error($"导入示例文档异常: {ex.Message}", "Chat", ex);
        }
        finally
        {
            IsBusy = false;
        }
    }

    /// <summary>点击新手快捷提问芯片直接发送问题。</summary>
    [RelayCommand]
    private async Task QuickAskAsync(string? question)
    {
        if (string.IsNullOrWhiteSpace(question) || IsBusy) return;
        InputText = question.Trim();
        await SendAsync();
    }

    /// <summary>导出当前对话记录为 Markdown 文件或复制到剪贴板。</summary>
    [RelayCommand]
    private void ExportChat()
    {
        if (Messages.Count == 0)
        {
            _notifications?.Warning("当前没有对话记录可导出");
            return;
        }

        var sb = new System.Text.StringBuilder();
        sb.AppendLine($"# DocMind 对话记录导出");
        sb.AppendLine($"- **导出时间**：{DateTime.Now:yyyy-MM-dd HH:mm:ss}");
        sb.AppendLine($"- **会话 ID**：{_chatId ?? "临时会话"}");
        sb.AppendLine($"- **使用模型**：{SelectedModel}");
        sb.AppendLine();
        sb.AppendLine("---");
        sb.AppendLine();

        int round = 1;
        foreach (var msg in Messages)
        {
            if (msg.Role == "user")
            {
                sb.AppendLine($"### 👤 用户 (第 {round} 轮)");
                sb.AppendLine(msg.Content);
                sb.AppendLine();
            }
            else if (msg.Role == "assistant")
            {
                sb.AppendLine($"### 🤖 DocMind 智能助手");
                sb.AppendLine(msg.Content);
                sb.AppendLine();
                if (msg.Sources != null && msg.Sources.Count > 0)
                {
                    sb.AppendLine("**📚 引用参考资料：**");
                    foreach (var s in msg.Sources)
                    {
                        sb.AppendLine($"- [{s.Index}] `{s.Source}` (相似度: {s.Score:F2})");
                    }
                    sb.AppendLine();
                }
                sb.AppendLine("---");
                sb.AppendLine();
                round++;
            }
        }

        var markdownText = sb.ToString();

        try
        {
            var saveFileDialog = new Microsoft.Win32.SaveFileDialog
            {
                Title = "导出对话记录",
                Filter = "Markdown 文件 (*.md)|*.md|文本文件 (*.txt)|*.txt|所有文件 (*.*)|*.*",
                FileName = $"DocMind_Chat_{DateTime.Now:yyyyMMdd_HHmmss}.md",
                DefaultExt = ".md"
            };

            if (saveFileDialog.ShowDialog() == true)
            {
                System.IO.File.WriteAllText(saveFileDialog.FileName, markdownText, System.Text.Encoding.UTF8);
                _notifications?.Success($"对话记录已成功导出至：{System.IO.Path.GetFileName(saveFileDialog.FileName)}");
                StatusMessage = $"已导出文件：{saveFileDialog.FileName}";
            }
            else
            {
                // 若用户取消保存对话框，则复制至剪贴板作为备选
                Clipboard.SetText(markdownText);
                _notifications?.Success("已将对话记录 (Markdown) 复制到剪贴板");
                StatusMessage = "对话记录已复制到剪贴板";
            }
        }
        catch (Exception ex)
        {
            DebugLog.Error($"导出对话记录异常: {ex.Message}", "Chat", ex);
            _notifications?.Error($"导出失败: {ex.Message}");
        }
    }

    /// <summary>在 Windows 文件资源管理器中定位当前引用的原文件。</summary>
    [RelayCommand]
    private void RevealSourceInExplorer()
    {
        if (SelectedSource is null || string.IsNullOrWhiteSpace(SelectedSource.Source)) return;

        var path = SelectedSource.Source;
        if (path.StartsWith("note:", StringComparison.OrdinalIgnoreCase))
        {
            _notifications?.Warning("该引用为即时沉淀笔记，非物理磁盘文件");
            return;
        }

        try
        {
            if (System.IO.File.Exists(path))
            {
                Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{path}\"") { UseShellExecute = true });
            }
            else if (System.IO.Directory.Exists(path))
            {
                Process.Start(new ProcessStartInfo("explorer.exe", $"\"{path}\"") { UseShellExecute = true });
            }
            else
            {
                _notifications?.Warning($"未在本地磁盘找到文件路径：{path}");
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"在资源管理器中定位失败: {ex.Message}", "Chat");
            _notifications?.Error($"定位文件失败: {ex.Message}");
        }
    }

    /// <summary>使用系统默认应用程序打开引用的原文件。</summary>
    [RelayCommand]
    private void OpenSourceFile()
    {
        if (SelectedSource is null || string.IsNullOrWhiteSpace(SelectedSource.Source)) return;

        var path = SelectedSource.Source;
        if (path.StartsWith("note:", StringComparison.OrdinalIgnoreCase))
        {
            _notifications?.Warning("该引用为即时沉淀笔记，非物理磁盘文件");
            return;
        }

        try
        {
            if (System.IO.File.Exists(path))
            {
                Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
            }
            else
            {
                _notifications?.Warning($"本地文件不存在或已被移动：{path}");
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"打开文件失败: {ex.Message}", "Chat");
            _notifications?.Error($"打开文件失败: {ex.Message}");
        }
    }

    /// <summary>对话页拉取模型列表（用后端运行时配置：设置页已保存的 provider/key/地址）。</summary>
    [RelayCommand]
    private async Task RefreshModelsAsync()
    {
        try
        {
            var result = await _apiService.LlmModelsAsync(new LlmModelsRequest { Timeout = 10 });
            if (!result.Ok)
            {
                StatusMessage = $"❌ 获取模型列表失败: {result.Error ?? "未知错误"}";
                DebugLog.Warn($"对话页获取模型列表失败: {result.Error}", "Chat");
                return;
            }

            var current = SelectedModel;
            AvailableModels.Clear();
            AvailableModels.Add(DefaultModelLabel);
            foreach (var m in result.Models)
            {
                if (!string.IsNullOrWhiteSpace(m))
                {
                    AvailableModels.Add(m);
                }
            }
            // 保留用户此前选择（已不在列表中则回到默认）
            SelectedModel = AvailableModels.Contains(current) ? current : DefaultModelLabel;
            StatusMessage = $"✅ 获取到 {result.Models.Count} 个模型（{result.Provider}）";
            DebugLog.Info($"对话页模型列表: provider={result.Provider} count={result.Models.Count}", "Chat");
        }
        catch (Exception ex)
        {
            StatusMessage = $"❌ 获取模型列表失败: {ex.Message}";
            DebugLog.Warn($"对话页获取模型列表异常: {ex.Message}", "Chat");
        }
    }

    /// <summary>种子模型：从后端配置取当前 llm_model 加入候选（未拉列表前至少能看到配置值）。</summary>
    private async Task SeedModelFromConfigAsync()
    {
        try
        {
            var cfg = await _apiService.GetConfigAsync();
            var model = cfg?.LlmModel;
            if (!string.IsNullOrWhiteSpace(model) && !AvailableModels.Contains(model))
            {
                AvailableModels.Add(model);
            }
        }
        catch (Exception ex)
        {
            DebugLog.Debug($"读取后端配置种子模型失败（忽略）: {ex.Message}", "Chat");
        }
    }

    /// <summary>测试用：等待会话列表加载（构造时 fire-and-forget 不可 await）。</summary>
    internal Task SessionsLoadedForTestAsync() => LoadSessionsAsync();

    /// <summary>测试用：等待选中会话的消息加载完成。</summary>
    internal Task SessionLoadedForTestAsync()
        => SelectedSession is null ? Task.CompletedTask : LoadSessionMessagesAsync(SelectedSession);
}

/// <summary>待发送附件项。</summary>
public sealed record AttachmentItem(string FullPath, string FileName, string Icon, string FileSizeText);