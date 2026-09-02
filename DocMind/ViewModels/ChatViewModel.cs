using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
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
    private string _statusText = string.Empty;
    private bool _showStatus;

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

    /// <summary>从纯 Markdown 内容特征推断交付物类型（模型未输出 :::artifact 包裹时的自愈推断，
    /// 对齐后端 parser.extract_artifact 的容错能力）。
    /// 只推断特征足够明确的 PPT / HTML，不推断普通文档，避免日常对话被误判成创作物。</summary>
    private static string? InferArtifactType(string content)
    {
        if (string.IsNullOrWhiteSpace(content) || content.Length < 200) return null;

        var lower = content.ToLowerInvariant();

        // 1) HTML：出现完整 HTML 文档标记
        if (lower.Contains("<!doctype html") || lower.Contains("<html")) return "html";

        // 2) PPT：多个 `---` 分页 + 幻灯片特征（板式标记或幻灯片关键词）
        var dashPages = System.Text.RegularExpressions.Regex.Split(content, @"(?m)^---\s*$");
        var hasSlideMarker = lower.Contains("<!-- layout:") || lower.Contains("<!-- note:");
        var hasSlideKeyword = lower.Contains("slide")
                              || lower.Contains("幻灯片")
                              || lower.Contains("ppt")
                              || lower.Contains("演示文稿")
                              || lower.Contains("演讲");
        if (dashPages.Length >= 3 && (hasSlideMarker || hasSlideKeyword)) return "pptx";

        return null;
    }

    /// <summary>从内容首个标题行推断交付物标题（自愈推断场景使用）。</summary>
    private static string InferArtifactTitle(string content)
    {
        foreach (var line in content.Split('\n'))
        {
            var ls = line.Trim();
            if (ls.StartsWith('#'))
            {
                return ls.TrimStart('#').Trim();
            }
        }
        return "知识创作交付物";
    }

    /// <summary>正文引用角标匹配：[1] / [1,2] / [1、2]。
    /// 边界仅用 ASCII 字符类（.NET 的 \w 会把中文算作单词字符，导致“见[1]”不匹配）；
    /// 前后粘着英文/数字/方括号时不转换，避免误伤代码里的下标如 arr[1]。</summary>
    private static readonly System.Text.RegularExpressions.Regex SourceMarkerRegex =
        new(@"(?<![A-Za-z0-9_\]])\[(\d{1,3}(?:[,\s、，]\d{1,3})*)\](?![A-Za-z0-9_\[])",
            System.Text.RegularExpressions.RegexOptions.Compiled);

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
            // 自愈容错：模型有时不写 :::artifact 包裹，只输出一份纯 Markdown 方案。
            // 此时先按内容特征推断类型并合成一个等价的 artifact 块，让后续统一走同一套解析逻辑，
            // 否则这类内容在气泡下完全没有「创作物工作台」预览入口。
            if (!ArtifactRegex.IsMatch(rawContent))
            {
                var inferredType = InferArtifactType(rawContent);
                if (inferredType != null)
                {
                    rawContent = $":::artifact type=\"{inferredType}\" title=\"{InferArtifactTitle(rawContent)}\" theme=\"tech_blue\"\n{rawContent}\n:::";
                }
            }
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

                        // 启发式指标抽取（命中的条目从要点中剔除，避免与 KPI 卡片重复渲染）
                        var metricRx = new System.Text.RegularExpressions.Regex(@"^([0-9]+(?:\.[0-9]+)?(?:%|x|X|ms|s|MB|GB|KB|倍|万|亿)?)\s*[:：\-—]\s*(.*)$");
                        var absorbed = new List<int>();
                        for (var bi = 0; bi < sBullets.Count; bi++)
                        {
                            var mm = metricRx.Match(sBullets[bi]);
                            if (mm.Success)
                            {
                                sMetrics.Add(new MetricItem { Value = mm.Groups[1].Value.Trim(), Label = mm.Groups[2].Value.Trim() });
                                absorbed.Add(bi);
                            }
                        }

                        // 启发式时间线抽取（同上，命中的条目剔除）
                        var timeRx = new System.Text.RegularExpressions.Regex(@"^(阶段[一二三四五六七八九十1-9]|Step\s*\d+|Q[1-4]|步骤[1-9])\s*[:：\-—]\s*(.*)$", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
                        for (var bi = 0; bi < sBullets.Count; bi++)
                        {
                            var tm = timeRx.Match(sBullets[bi]);
                            if (tm.Success)
                            {
                                sTimeline.Add(new TimelineNodeItem { Stage = tm.Groups[1].Value.Trim(), Title = tm.Groups[2].Value.Trim() });
                                if (!absorbed.Contains(bi)) absorbed.Add(bi);
                            }
                        }

                        // 已被指标 / 时间线卡片吸收的要点不再重复展示
                        foreach (var bi in absorbed.OrderByDescending(i => i))
                        {
                            if (bi < sBullets.Count) sBullets.RemoveAt(bi);
                        }

                        // 板式智能裁决（预览卡片中各板式区域叠加展示，此处仅用于识别主视觉形态）
                        if (sLayout == "general")
                        {
                            // 仅当首页确实带副标题时才认定为封面，避免首页正文被封面框架吞掉
                            if (sIndex == 1 && !string.IsNullOrEmpty(sSub)) sLayout = "cover";
                            else if (sCards.Count >= 2 && sCards.Count <= 4) sLayout = "cards";
                            else if (sBullets.Count == 0 && sMetrics.Count >= 2) sLayout = "metrics";
                            else if (sBullets.Count == 0 && sTimeline.Count >= 2) sLayout = "timeline";
                            else if (sTable.Count >= 2) sLayout = "table";
                            else if (!string.IsNullOrEmpty(sQuote)) sLayout = "quote";
                            }

                            // 主视觉唯一化：被更特殊主视觉覆盖的块级数据降级为补充要点，避免多块大视觉堆叠导致预览“混乱”，同时不丢失内容。
                            // 注：general/agenda/cover 页的卡片仍由卡片区渲染，不在此降级（否则会与卡片区重复）。
                            if (sLayout != "cards" && sLayout != "general" && sLayout != "agenda" && sLayout != "cover")
                            {
                                foreach (var c in sCards)
                                {
                                    var ct = string.IsNullOrEmpty(c.Content) ? c.Title : $"{c.Title}：{c.Content}";
                                    if (c.Bullets.Count > 0) ct += "（" + string.Join("；", c.Bullets) + "）";
                                    sBullets.Add(ct);
                                }
                            }
                            if (sLayout != "metrics")
                                foreach (var m in sMetrics) sBullets.Add($"{m.Value} {m.Label}".Trim());
                            if (sLayout != "timeline")
                                foreach (var t in sTimeline) sBullets.Add($"{t.Stage}：{t.Title}");
                            if (sLayout != "quote" && !string.IsNullOrWhiteSpace(sQuote))
                                sBullets.Add(sQuote);

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
                // 通知 ViewModel 触发自动导出（仅在流式完成时）
                ArtifactParsed?.Invoke(this, item);
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
            // 降级标题层级：Markdig 把 ###/#### 渲染成 18px+ 大标题，气泡读起来像文档报告；
            // 统一压回正文大小（保留加粗层级），让回答更接近自然聊天的语气。
            DownscaleHeadings(doc);
            // 让回答正文里的 Markdown 链接可直接点击（仅 http/https，其余不可点击防风险）
            AttachLinkNavigation(doc);
            // 把 [n] 引用标记转换为可点击角标（点击打开对应来源抽屉）
            WireSourceMarkers(doc);
            RenderedDocument = doc;
        }
        catch (Exception ex)
        {
            // 解析失败:清空 RenderedDocument,UI 会 fallback 到纯文本兜底 TextBox(由 Visibility 控制)。
            // 记录原文长度与片段：排查"回答只剩标题/代码块消失"时，
            // 需要能区分是原文本身不完整，还是渲染层把内容丢了。
            var preview = Content.Length > 200 ? Content[..200] + "…" : Content;
            DebugLog.Warn(
                $"Markdown 解析失败,降级纯文本: 原文 {Content.Length} 字, {ex.GetType().Name}: {ex.Message}\n" +
                $"  原文片段: {preview}", "Chat");
            if (_renderedDocument is not null)
            {
                RenderedDocument = null;
            }
        }
    }

    /// <summary>把 Markdig 渲染的大标题（### 18px Bold 等）降级为正文大小加粗，
    /// 消除气泡的"文档报告感"，让 AI 回答更像聊天。</summary>
    private static void DownscaleHeadings(FlowDocument doc)
    {
        foreach (var block in doc.Blocks)
        {
            DownscaleHeadings(block);
        }
    }

    private static void DownscaleHeadings(Block block)
    {
        switch (block)
        {
            case Paragraph p:
                // 只有标题会被 Markdig 显式放大到 18px+；正文/代码块继承 15px 不受影响
                if (p.FontSize > 15)
                {
                    p.FontSize = 15;
                    p.FontWeight = FontWeights.SemiBold;
                }
                break;
            case List list:
                foreach (var item in list.ListItems)
                {
                    foreach (var itemBlock in item.Blocks)
                    {
                        DownscaleHeadings(itemBlock);
                    }
                }
                break;
            case Table table:
                foreach (var group in table.RowGroups)
                {
                    foreach (var row in group.Rows)
                    {
                        foreach (var cell in row.Cells)
                        {
                            foreach (var cellBlock in cell.Blocks)
                            {
                                DownscaleHeadings(cellBlock);
                            }
                        }
                    }
                }
                break;
            case Section section:
                foreach (var child in section.Blocks)
                {
                    DownscaleHeadings(child);
                }
                break;
        }
    }

    /// <summary>让回答正文中的 Markdown 链接可直接点击并在默认浏览器打开。
    /// Markdig.Wpf 会把 <c>[text](url)</c> 渲染成带 NavigateUri 的 Hyperlink，但默认
    /// 没有任何导航处理器，点击无反应。这里只对 http/https 绝对链接挂
    /// RequestNavigate 处理器；javascript:/file:/相对路径等一律移除导航（不可点击），
    /// 防止 AI 输出或文档中的危险协议被直接打开。</summary>
    private static void AttachLinkNavigation(FlowDocument doc)
    {
        foreach (var block in doc.Blocks)
        {
            AttachLinkNavigation(block);
        }
    }

    private static void AttachLinkNavigation(Block block)
    {
        switch (block)
        {
            case Paragraph paragraph:
                foreach (var inline in paragraph.Inlines)
                {
                    AttachLinkNavigation(inline);
                }
                break;
            case List list:
                foreach (var item in list.ListItems)
                {
                    foreach (var itemBlock in item.Blocks)
                    {
                        AttachLinkNavigation(itemBlock);
                    }
                }
                break;
            case Table table:
                foreach (var group in table.RowGroups)
                {
                    foreach (var row in group.Rows)
                    {
                        foreach (var cell in row.Cells)
                        {
                            foreach (var cellBlock in cell.Blocks)
                            {
                                AttachLinkNavigation(cellBlock);
                            }
                        }
                    }
                }
                break;
            case Section section:
                foreach (var child in section.Blocks)
                {
                    AttachLinkNavigation(child);
                }
                break;
        }
    }

    private static void AttachLinkNavigation(Inline inline)
    {
        switch (inline)
        {
            case Hyperlink link:
                WireHyperlink(link);
                // 链接文本内可能再嵌套行内元素（如代码 span）
                foreach (var child in link.Inlines)
                {
                    AttachLinkNavigation(child);
                }
                break;
            case Span span:
                foreach (var child in span.Inlines)
                {
                    AttachLinkNavigation(child);
                }
                break;
        }
    }

    private static void WireHyperlink(Hyperlink link)
    {
        var raw = link.NavigateUri?.ToString() ?? "";
        if (Uri.TryCreate(raw, UriKind.Absolute, out var uri)
            && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps))
        {
            link.NavigateUri = uri;
            // 主题色高亮 + 下划线：FlowDocument 设置了 TextPrimary 前景，
            // 默认会让链接继承成正文颜色而看不出可点击，这里显式恢复链接样式
            link.Foreground = (Brush)(System.Windows.Application.Current?.FindResource("PrimaryBrush")
                                      ?? Brushes.DodgerBlue);
            link.TextDecorations = TextDecorations.Underline;
            link.RequestNavigate += (_, e) =>
            {
                e.Handled = true;
                try
                {
                    System.Diagnostics.Process.Start(
                        new System.Diagnostics.ProcessStartInfo(uri.AbsoluteUri)
                        {
                            UseShellExecute = true,
                        });
                }
                catch (Exception ex)
                {
                    DebugLog.Warn($"打开链接失败: {ex.Message}", "Chat");
                }
            };
        }
        else
        {
            // 非 http(s) 链接：移除导航，点击无反应（防止 javascript:/file: 等风险）
            link.NavigateUri = null;
        }
    }

    /// <summary>把正文中的 [n] 引用标记转换为可点击角标（DeepSeek 风格），
    /// 点击打开对应来源抽屉。仅在消息带来源时生效；代码块段落（带样式）跳过，
    /// 避免把代码里的下标当作引用。</summary>
    private void WireSourceMarkers(FlowDocument doc)
    {
        if (Sources is not { Count: > 0 })
        {
            return;
        }
        // 注意：遍历必须用快照——对 Paragraph.Inlines 的 Clear/Add 会递增整个
        // TextContainer 的版本号，使正在进行的 doc.Blocks 枚举器直接失效（WPF 经典坑）。
        foreach (var block in doc.Blocks.ToList())
        {
            WireSourceMarkers(block);
        }
    }

    private void WireSourceMarkers(Block block)
    {
        switch (block)
        {
            case Paragraph paragraph:
                WireSourceMarkers(paragraph);
                break;
            case List list:
                foreach (var item in list.ListItems.ToList())
                {
                    foreach (var itemBlock in item.Blocks.ToList())
                    {
                        WireSourceMarkers(itemBlock);
                    }
                }
                break;
            case Table table:
                foreach (var group in table.RowGroups.ToList())
                {
                    foreach (var row in group.Rows.ToList())
                    {
                        foreach (var cell in row.Cells.ToList())
                        {
                            foreach (var cellBlock in cell.Blocks.ToList())
                            {
                                WireSourceMarkers(cellBlock);
                            }
                        }
                    }
                }
                break;
            case Section section:
                foreach (var child in section.Blocks.ToList())
                {
                    WireSourceMarkers(child);
                }
                break;
        }
    }

    private void WireSourceMarkers(Paragraph paragraph)
    {
        // Markdig.Wpf 渲染代码块时会给段落套样式；带样式的段落跳过，避免误转换
        if (paragraph.Style is not null)
        {
            return;
        }
        var inlines = paragraph.Inlines.ToList();
        // Markdig 会把 [ 拆成独立 Run（如 Run[官方资料见] Run[[] Run[1]…]），
        // 引用标记跨多个 Run 导致单 Run 匹配不到。先把格式相同的相邻 Run 合并成一段。
        var merged = new List<Inline>();
        foreach (var inline in inlines)
        {
            if (inline is Run run && merged.LastOrDefault() is Run last && SameFormat(last, run))
            {
                last.Text += run.Text;
            }
            else
            {
                merged.Add(inline);
            }
        }
        paragraph.Inlines.Clear();
        foreach (var inline in merged)
        {
            if (inline is Run run)
            {
                AppendRunWithMarkers(paragraph, run);
            }
            else
            {
                paragraph.Inlines.Add(inline);
            }
        }
    }

    private void AppendRunWithMarkers(Paragraph paragraph, Run run)
    {
        var text = run.Text ?? "";
        var matches = SourceMarkerRegex.Matches(text);
        if (matches.Count == 0)
        {
            paragraph.Inlines.Add(run);
            return;
        }

        var validIndexes = new HashSet<int>();
        if (Sources is not null)
        {
            foreach (var src in Sources)
            {
                validIndexes.Add(src.Index);
            }
        }

        int pos = 0;
        foreach (System.Text.RegularExpressions.Match match in matches)
        {
            if (match.Index > pos)
            {
                paragraph.Inlines.Add(CloneRun(run, text[pos..match.Index]));
            }
            var firstIndex = int.Parse(match.Groups[1].Value.Split(',', '，', '、', ' ')[0]);
            if (validIndexes.Contains(firstIndex))
            {
                paragraph.Inlines.Add(CreateSourceMarker(run, match.Groups[1].Value.Trim(), firstIndex));
            }
            else
            {
                // 索引不在来源范围内：原样保留文本，避免误转换
                paragraph.Inlines.Add(CloneRun(run, match.Value));
            }
            pos = match.Index + match.Length;
        }
        if (pos < text.Length)
        {
            paragraph.Inlines.Add(CloneRun(run, text[pos..]));
        }
    }

    /// <summary>两个 Run 的格式是否一致（可安全合并文本）。</summary>
    private static bool SameFormat(Run a, Run b)
        => Equals(a.FontFamily, b.FontFamily)
           && Equals(a.FontSize, b.FontSize)
           && Equals(a.FontWeight, b.FontWeight)
           && Equals(a.FontStyle, b.FontStyle)
           && Equals(a.Foreground, b.Foreground)
           && Equals(a.Background, b.Background)
           && (a.TextDecorations?.Count ?? 0) == (b.TextDecorations?.Count ?? 0);

    /// <summary>复制 Run 的格式（加粗/斜体/前景/字号等）以保留分段后的视觉效果。</summary>
    private static Run CloneRun(Run template, string text)
    {
        var run = new Run(text)
        {
            FontFamily = template.FontFamily,
            FontSize = template.FontSize,
            FontWeight = template.FontWeight,
            FontStyle = template.FontStyle,
            Foreground = template.Foreground,
            Background = template.Background,
            TextDecorations = template.TextDecorations,
        };
        return run;
    }

    /// <summary>创建引用角标（高质感交互徽章，带悬浮详细来源卡片与直达小图标，点击打开来源或网页）。</summary>
    private Hyperlink CreateSourceMarker(Run template, string indexText, int index)
    {
        var targetSource = Sources?.FirstOrDefault(s => s.Index == index);
        var isWeb = targetSource?.IsWebSource == true && !string.IsNullOrWhiteSpace(targetSource.Url);
        var iconSymbol = isWeb ? "🌐↗" : "📄";

        var link = new Hyperlink
        {
            Foreground = (Brush)(System.Windows.Application.Current?.FindResource("PrimaryBrush")
                                 ?? Brushes.DodgerBlue),
            FontSize = Math.Max(10, (template.FontSize > 0 ? template.FontSize : 15) - 3),
            FontWeight = FontWeights.Bold,
            BaselineAlignment = BaselineAlignment.Superscript,
            TextDecorations = null,
            Cursor = Cursors.Hand,
        };

        if (targetSource != null)
        {
            var tip = new ToolTip
            {
                Background = (Brush)(System.Windows.Application.Current?.FindResource("CardBrush") ?? Brushes.White),
                BorderBrush = (Brush)(System.Windows.Application.Current?.FindResource("BorderBrush") ?? Brushes.LightGray),
                BorderThickness = new Thickness(1),
                Padding = new Thickness(8, 6, 8, 6),
            };
            var tipPanel = new StackPanel { MaxWidth = 340 };
            var tipHeader = new TextBlock
            {
                Text = targetSource.IsWebSource ? $"🌐 [{index}] {targetSource.DisplayTitle}" : $"📄 [{index}] {targetSource.DisplayTitle}",
                FontWeight = FontWeights.SemiBold,
                FontSize = 12,
                TextWrapping = TextWrapping.Wrap,
                Foreground = (Brush)(System.Windows.Application.Current?.FindResource("TextPrimaryBrush") ?? Brushes.Black),
            };
            tipPanel.Children.Add(tipHeader);

            if (targetSource.IsWebSource && !string.IsNullOrWhiteSpace(targetSource.Url))
            {
                var tipUrl = new TextBlock
                {
                    Text = targetSource.Url,
                    FontSize = 10,
                    Foreground = (Brush)(System.Windows.Application.Current?.FindResource("PrimaryBrush") ?? Brushes.DodgerBlue),
                    TextTrimming = TextTrimming.CharacterEllipsis,
                    Margin = new Thickness(0, 2, 0, 0),
                };
                tipPanel.Children.Add(tipUrl);
            }
            else if (!targetSource.IsWebSource)
            {
                var tipMeta = new TextBlock
                {
                    Text = targetSource.Page.HasValue ? $"页码: P{targetSource.Page.Value} · 章节: {targetSource.Heading ?? "正文"}" : $"章节: {targetSource.Heading ?? "正文"}",
                    FontSize = 10,
                    Foreground = (Brush)(System.Windows.Application.Current?.FindResource("TextSecondaryBrush") ?? Brushes.Gray),
                    Margin = new Thickness(0, 2, 0, 0),
                };
                tipPanel.Children.Add(tipMeta);
            }

            if (!string.IsNullOrWhiteSpace(targetSource.Snippet))
            {
                var tipSnippet = new TextBlock
                {
                    Text = targetSource.Snippet.Trim(),
                    FontSize = 11,
                    Foreground = (Brush)(System.Windows.Application.Current?.FindResource("TextSecondaryBrush") ?? Brushes.DarkGray),
                    TextWrapping = TextWrapping.Wrap,
                    MaxHeight = 80,
                    TextTrimming = TextTrimming.CharacterEllipsis,
                    Margin = new Thickness(0, 4, 0, 0),
                };
                tipPanel.Children.Add(tipSnippet);
            }

            var tipHint = new TextBlock
            {
                Text = isWeb ? "💡 点击直接在默认浏览器中打开该网页（同时展示来源抽屉）" : "💡 点击直接打开对应来源详情与切片原文",
                FontSize = 10,
                Foreground = (Brush)(System.Windows.Application.Current?.FindResource("TextTertiaryBrush") ?? Brushes.Gray),
                Margin = new Thickness(0, 6, 0, 0),
            };
            tipPanel.Children.Add(tipHint);
            tip.Content = tipPanel;
            link.ToolTip = tip;

            // 点击角标：如果为网页且带有合法 URL，直接在默认浏览器中打开；并通知展开抽屉
            link.Click += (_, _) =>
            {
                if (isWeb && ChatViewModel.TryOpenHttpUrl(targetSource.Url!))
                {
                    // 默认浏览器已触发
                }
                NotifySourceMarker(index);
            };
        }
        else
        {
            link.ToolTip = $"查看来源 [{indexText}]";
            link.Click += (_, _) => NotifySourceMarker(index);
        }

        link.Inlines.Add(new Run($"[{indexText} {iconSymbol}]"));
        return link;
    }

    /// <summary>引用来源（仅 assistant 有）。设置后刷新搜索摘要/状态统计等计算属性。</summary>
    public IReadOnlyList<SourceRef>? Sources
    {
        get => _sources;
        set
        {
            if (SetField(ref _sources, value))
            {
                foreach (var name in new[]
                {
                    nameof(HasSources), nameof(WebSourceCount), nameof(WebFetchedCount),
                    nameof(HasWebSources), nameof(SearchSummaryText),
                    nameof(WebSources), nameof(LocalSources),
                    nameof(TokenStatText), nameof(HasTokenStat),
                })
                {
                    PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(name));
                }
                // 当 Sources 到达后，重新解析正文以挂载来源引用角标与悬浮卡片
                if (!string.IsNullOrEmpty(Content))
                {
                    UpdateRenderedDocument(force: true);
                }
            }
        }
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

    /// <summary>耗时 ms（仅 assistant 有）。设置后刷新底部状态统计。</summary>
    public int? ElapsedMs
    {
        get => _elapsedMs;
        set
        {
            if (SetField(ref _elapsedMs, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(TokenStatText)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasTokenStat)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingHeaderText)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
            }
        }
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
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingHeaderText)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
            }
        }
    }

    /// <summary>是否正在等待首个 token返回（控制骨架屏可见性）。</summary>
    public bool IsWaitingForFirstToken
    {
        get => _isWaitingForFirstToken;
        set
        {
            if (SetField(ref _isWaitingForFirstToken, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingHeaderText)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
            }
        }
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

    /// <summary>流式阶段状态文案（正在检索/正在联网搜索...）。</summary>
    public string StatusText
    {
        get => _statusText;
        set => SetField(ref _statusText, value);
    }

    /// <summary>是否显示阶段状态指示器。</summary>
    public bool ShowStatus
    {
        get => _showStatus;
        set => SetField(ref _showStatus, value);
    }

    // ===== DeepSeek 风格信息卡片：思考过程 / 搜索摘要 / 状态统计 =====

    /// <summary>流式阶段收集的思考/搜索过程步骤（解析附件、检索知识库、联网搜索、生成…）。</summary>
    public ObservableCollection<string> ThinkingSteps { get; } = new();

    /// <summary>是否有思考过程可展示（控制折叠区可见性）。</summary>
    public bool HasThinkingSteps => ThinkingSteps.Count > 0;

    /// <summary>是否有任何真实思考内容（链路步骤 或 模型推理链 reasoning_content）。</summary>
    public bool HasThinking => HasThinkingSteps || HasThinkingText;

    private bool _isThinkingExpanded = true;

    /// <summary>思考过程区是否展开（点击标题切换）。</summary>
    public bool IsThinkingExpanded
    {
        get => _isThinkingExpanded;
        set => SetField(ref _isThinkingExpanded, value);
    }

    private bool _isThinkingInProgress;

    /// <summary>是否处于思考进行中阶段（生成中且尚未输出正文）。</summary>
    public bool IsThinkingInProgress
    {
        get => _isThinkingInProgress;
        set
        {
            if (SetField(ref _isThinkingInProgress, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingHeaderText)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
            }
        }
    }

    private string _thinkingDurationText = "";

    /// <summary>思考耗时文案（如「用时 5.2 秒」）。</summary>
    public string ThinkingDurationText
    {
        get => _thinkingDurationText;
        set
        {
            if (SetField(ref _thinkingDurationText, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingHeaderText)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
            }
        }
    }

    /// <summary>思考过程标题：生成中显示「思考中...」，完成后显示「已思考（用时 X 秒）」。</summary>
    public string ThinkingHeaderText
    {
        get
        {
            if (IsLoading && (IsThinkingInProgress || IsWaitingForFirstToken))
            {
                return !string.IsNullOrEmpty(ThinkingDurationText)
                    ? $"思考中 ({ThinkingDurationText})"
                    : "思考中...";
            }
            return !string.IsNullOrEmpty(ThinkingDurationText)
                ? $"已思考（{ThinkingDurationText}）"
                : "已思考";
        }
    }

    /// <summary>思考状态图标文字（🧠 思考中 / 💭 已完成 / 空 无思考）。</summary>
    public string ThinkingIconText
    {
        get
        {
            if (IsLoading && (IsThinkingInProgress || IsWaitingForFirstToken))
                return "🧠";
            return HasThinking ? "💭" : "";
        }
    }

    private FlowDocument? _renderedThinkingDocument;
    private long _lastThinkingRenderTicks;

    /// <summary>模型推理链 Markdown 解析后的 FlowDocument。</summary>
    public FlowDocument? RenderedThinkingDocument
    {
        get => _renderedThinkingDocument;
        private set => SetField(ref _renderedThinkingDocument, value);
    }

    private void UpdateRenderedThinkingDocument(bool force = false)
    {
        if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
        {
            dispatcher.InvokeAsync(() => UpdateRenderedThinkingDocument(force));
            return;
        }

        if (string.IsNullOrWhiteSpace(ThinkingText))
        {
            if (_renderedThinkingDocument is not null)
            {
                RenderedThinkingDocument = null;
            }
            return;
        }

        var now = Environment.TickCount64;
        if (!force && (now - _lastThinkingRenderTicks) < RenderThrottleMs)
        {
            return;
        }
        _lastThinkingRenderTicks = now;

        try
        {
            var pipeline = new MarkdownPipelineBuilder().UseSupportedExtensions().Build();
            var doc = Markdig.Wpf.Markdown.ToFlowDocument(ThinkingText, pipeline);
            doc.PagePadding = new Thickness(0);
            doc.FontFamily = SystemFonts.MessageFontFamily;
            doc.FontSize = 12.5;
            doc.FontWeight = FontWeights.Normal;
            doc.Foreground = (Brush)(System.Windows.Application.Current?.FindResource("TextSecondaryBrush")
                                     ?? Brushes.Gray);
            DownscaleHeadings(doc);
            AttachLinkNavigation(doc);
            RenderedThinkingDocument = doc;
        }
        catch
        {
            RenderedThinkingDocument = null;
        }
    }

    private string _thinkingText = "";

    /// <summary>模型真实推理链文本（DeepSeek-R1/Qwen3 的 reasoning_content 增量累积）。</summary>
    public string ThinkingText
    {
        get => _thinkingText;
        private set
        {
            if (SetField(ref _thinkingText, value))
            {
                UpdateRenderedThinkingDocument();
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasThinkingText)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasThinking)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
            }
        }
    }

    /// <summary>是否有模型推理链可展示。</summary>
    public bool HasThinkingText => !string.IsNullOrWhiteSpace(ThinkingText);

    /// <summary>追加一段推理链文本（流式增量）。</summary>
    public void AppendThinking(string text)
    {
        if (string.IsNullOrEmpty(text))
        {
            return;
        }
        ThinkingText += text;
    }

    /// <summary>更新流式进行中的实时思考秒数（由 ViewModel 定时器驱动）。</summary>
    public void UpdateLiveThinkingDuration(long elapsedMs)
    {
        if (IsThinkingInProgress || IsWaitingForFirstToken)
        {
            _thinkingDurationText = $"{Math.Max(0.1, elapsedMs / 1000.0):F1} 秒";
            PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingDurationText)));
            PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingHeaderText)));
            PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
        }
    }

    /// <summary>结束思考阶段，锁定最终耗时并切换为「已思考」。</summary>
    public void CompleteThinking(long elapsedMs)
    {
        IsThinkingInProgress = false;
        IsWaitingForFirstToken = false;
        ShowStatus = false;
        _thinkingDurationText = $"{Math.Max(0.1, elapsedMs / 1000.0):F1} 秒";
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(IsThinkingInProgress)));
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingDurationText)));
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingHeaderText)));
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
        UpdateRenderedThinkingDocument(force: true);
    }

    /// <summary>联网来源数（搜索到的网页数）。</summary>
    public int WebSourceCount => Sources?.Count(s => s.IsWebSource) ?? 0;

    /// <summary>实际抓到正文的网页数（浏览过的页面）。</summary>
    public int WebFetchedCount => Sources?.Count(s => s.IsWebSource && s.ContentFetched) ?? 0;

    /// <summary>真实互联网检索网页卡片列表。</summary>
    public IEnumerable<SourceRef> WebSources => Sources?.Where(s => s.IsWebSource) ?? Enumerable.Empty<SourceRef>();

    /// <summary>真实本地知识库原著切片卡片列表。</summary>
    public IEnumerable<SourceRef> LocalSources => Sources?.Where(s => !s.IsWebSource) ?? Enumerable.Empty<SourceRef>();

    /// <summary>是否有联网来源（控制搜索摘要行可见性）。</summary>
    public bool HasWebSources => WebSourceCount > 0;

    /// <summary>搜索摘要文案（如「搜索到 19 个网页 · 浏览 4 个页面」）。</summary>
    public string SearchSummaryText => HasWebSources
        ? $"搜索到 {WebSourceCount} 个网页 · 浏览 {WebFetchedCount} 个页面"
        : "";

    private int _tokenCount;

    /// <summary>本次回答的 token 数（客户端流式帧计数）。</summary>
    public int TokenCount
    {
        get => _tokenCount;
        set
        {
            if (SetField(ref _tokenCount, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(TokenStatText)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasTokenStat)));
            }
        }
    }

    /// <summary>底部状态统计文案（token · 速度 · 来源数 · 耗时）。</summary>
    public string TokenStatText
    {
        get
        {
            var parts = new List<string>();
            if (TokenCount > 0)
            {
                parts.Add($"{TokenCount} 帧");
                if (ElapsedMs is > 0)
                {
                    var secs = ElapsedMs.Value / 1000.0;
                    if (secs > 0)
                    {
                        parts.Add($"{TokenCount / secs:F1} 帧/s");
                    }
                }
            }
            if (Sources is { Count: > 0 })
            {
                parts.Add($"{Sources.Count} 个来源");
            }
            if (ElapsedMs is > 0)
            {
                parts.Add($"{ElapsedMs.Value}ms");
            }
            return string.Join(" · ", parts);
        }
    }

    /// <summary>是否有状态统计可展示（控制底部状态栏可见性）。</summary>
    public bool HasTokenStat => TokenCount > 0 || Sources is { Count: > 0 } || ElapsedMs is > 0;

    /// <summary>用户点击正文引用角标 [n] 时触发（n 为来源索引）。</summary>
    public event Action<int>? SourceMarkerRequested;

    /// <summary>创作物解析完成时触发（供 ViewModel 订阅以自动导出）。</summary>
    public event Action<object, ArtifactItem>? ArtifactParsed;

    /// <summary>
    /// 追加一条思考/搜索步骤（去重连续重复）。
    /// 后端会先发「正在…」进行中状态，完成后发「✔ 详情」状态；
    /// 收到「✔」时替换上一条「正在…」步骤，保持每一步紧凑且带详细内容。
    /// </summary>
    public void AddThinkingStep(string step)
    {
        var text = step?.Trim() ?? "";
        if (text.Length == 0)
        {
            return;
        }
        if (ThinkingSteps.Count > 0 && ThinkingSteps[^1] == text)
        {
            return;
        }
        // 「✔ 详情」替换上一条「正在…」进行中步骤
        if (text.StartsWith("✔") && ThinkingSteps.Count > 0 && ThinkingSteps[^1].StartsWith("正在"))
        {
            ThinkingSteps[^1] = text;
            return;
        }
        ThinkingSteps.Add(text);
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasThinkingSteps)));
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasThinking)));
    }

    /// <summary>生成失败/中断时收尾思考步骤：把末尾「正在…」替换为「✖」标记，
    /// 避免错误提示旁仍挂着进行中状态（调用方需配合 CompleteThinking 关闭阶段胶囊）。</summary>
    public void FailThinkingStep(string? reason, string label = "回答生成失败")
    {
        var detail = (reason ?? "").Trim();
        var nl = detail.IndexOfAny(['\n', '\r']);
        if (nl >= 0)
        {
            detail = detail[..nl].Trim();
        }
        if (detail.Length > 80)
        {
            detail = detail[..80] + "…";
        }
        var step = string.IsNullOrEmpty(detail) ? $"✖ {label}" : $"✖ {label}：{detail}";
        if (ThinkingSteps.Count > 0 && ThinkingSteps[^1].StartsWith("正在", StringComparison.Ordinal))
        {
            ThinkingSteps[^1] = step;
        }
        else if (ThinkingSteps.Count == 0 || ThinkingSteps[^1] != step)
        {
            ThinkingSteps.Add(step);
        }
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasThinkingSteps)));
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasThinking)));
    }

    /// <summary>触发引用角标点击（由正文中的角标 Hyperlink 调用）。</summary>
    public void NotifySourceMarker(int index) => SourceMarkerRequested?.Invoke(index);

    /// <summary>是否有可复制内容（控制「复制」按钮可见性）。</summary>
    public bool CanCopy => !IsLoading && !string.IsNullOrEmpty(Content);

    private bool _isCopied;

    /// <summary>是否已复制（用于呈现「已复制 ✓」对勾微动效）。</summary>
    public bool IsCopied
    {
        get => _isCopied;
        set => SetField(ref _isCopied, value);
    }

    private bool _isLiked;

    /// <summary>点赞状态。</summary>
    public bool IsLiked
    {
        get => _isLiked;
        set
        {
            if (SetField(ref _isLiked, value))
            {
                if (value && _isDisliked) IsDisliked = false;
            }
        }
    }

    private bool _isDisliked;

    /// <summary>点踩状态。</summary>
    public bool IsDisliked
    {
        get => _isDisliked;
        set
        {
            if (SetField(ref _isDisliked, value))
            {
                if (value && _isLiked) IsLiked = false;
            }
        }
    }

    /// <summary>切换点赞。</summary>
    [RelayCommand]
    private void ToggleLike()
    {
        IsLiked = !IsLiked;
    }

    /// <summary>切换点踩。</summary>
    [RelayCommand]
    private void ToggleDislike()
    {
        IsDisliked = !IsDisliked;
    }

    /// <summary>复制消息内容到剪贴板，并触发「已复制 ✓」反馈。</summary>
    [RelayCommand]
    private async Task CopyAsync()
    {
        if (string.IsNullOrEmpty(Content))
        {
            return;
        }
        try
        {
            Clipboard.SetText(Content);
            IsCopied = true;
            await Task.Delay(1500);
            IsCopied = false;
        }
        catch (Exception ex)
        {
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

    private string _configuredProvider = "none";
    private string _configuredModel = "";

    /// <summary>「默认提供商（设置页全局配置）」分组的候选模型：本地 AppSettings 种子 + 🔄 拉取结果。
    /// 点选该组模型只随请求带 model 参数覆盖，不携带 ProviderConfig、不改全局配置。</summary>
    private readonly List<string> _defaultProviderModels = new();

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

    /// <summary>创作类人设集合（后端自动路由创作意图时使用）。
    /// 仅当后端回传的实际生效人设属于此集合时，才静默同步前端人设下拉框，
    /// 避免把用户手动选择的 architect/brainstorm/office 覆写为回传值。</summary>
    private static readonly HashSet<string> CreativePersonaIds = new() { "ppt", "doc", "lesson", "table", "web" };

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

    /// <summary>是否开启实时联网搜索（持久化：勾选状态重启后保持）。</summary>
    public bool IsWebSearchEnabled
    {
        get => _isWebSearchEnabled;
        set
        {
            if (SetProperty(ref _isWebSearchEnabled, value))
            {
                // 勾选/取消即落盘，下次启动保持同样状态，避免每次重新勾选
                _appSettings.EnableWebSearch = value;
                try
                {
                    _appSettings.Save();
                }
                catch
                {
                    // 落盘失败不阻断对话
                }
            }
        }
    }

    public const string DefaultProfileLabel = "默认（设置页配置）";

    /// <summary>模型选择器候选（首项为「设置页默认」伪值，其后为各启用服务商的模型，显示「模型名（服务商名）」）。</summary>
    public ObservableCollection<ModelChoice> ModelChoices { get; } = new();

    private readonly AppSettings _appSettings;
    private ModelChoice? _selectedModelChoice;
    private bool _isWebSearchEnabled;

    /// <summary>构造期间抑制选择持久化（避免初始 RebuildModelChoices 覆盖上次落盘的模型选择）。</summary>
    private bool _isInitializingModelChoice;

    /// <summary>当前选中模型项；选中自定义服务商的模型 → 记录该服务商（发送时随请求携带 ProviderConfig）；默认项 → 用后端全局配置。</summary>
    public ModelChoice? SelectedModelChoice
    {
        get => _selectedModelChoice;
        set
        {
            if (SetProperty(ref _selectedModelChoice, value) && value is not null)
            {
                // 点选即持久化：新建会话/重启后还原上次选择的模型（与 EnableWebSearch 同机制）
                if (!_isInitializingModelChoice)
                {
                    PersistModelChoice(value);
                }
                // 单一事实源：Effective*/SelectedModel 均由此派生，避免双轨状态不同步
                OnPropertyChanged(nameof(SelectedModel));
                OnPropertyChanged(nameof(EffectiveProvider));
                OnPropertyChanged(nameof(EffectiveModel));
                OnPropertyChanged(nameof(EffectiveModelSummary));
                OnPropertyChanged(nameof(IsLlmConfigured));
                OnPropertyChanged(nameof(EmptyGuideText));
                StatusMessage = value.IsDefault ? "就绪" : $"模型: {value.DisplayName}（下条消息生效）";
            }
        }
    }

    /// <summary>把对话页模型选择落盘（AppSettings.LastChatModel/LastChatProfileId），重启/新建对话后还原。
    /// 默认项 → 两项皆空；默认提供商分组（裸模型名）→ 仅模型名；某服务商模型 → 模型名 + 档案 Id。</summary>
    private void PersistModelChoice(ModelChoice choice)
    {
        _appSettings.LastChatProfileId = choice.Provider?.Id;
        _appSettings.LastChatModel = choice.IsDefault ? null : choice.Model;
        try
        {
            _appSettings.Save();
        }
        catch
        {
            // 落盘失败不阻断对话
        }
    }

    public ChatViewModel(IDoc2kbApiService apiService, NotificationService? notifications = null, AppSettings? appSettings = null)
    {
        _apiService = apiService;
        _notifications = notifications;
        _appSettings = appSettings ?? new AppSettings();
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

        // 默认提供商（设置页全局配置）候选模型：先用本地 AppSettings 同步种子，后端拉回后再补充。
        // provider 无条件同步（IsLlmConfigured/EffectiveProvider 的判断依据）；
        // 模型名为空时仅跳过默认分组种子（无法预设具体模型）。
        _configuredProvider = string.IsNullOrWhiteSpace(_appSettings.LlmProvider) ? "none" : _appSettings.LlmProvider;
        if (!string.IsNullOrWhiteSpace(_appSettings.LlmModel))
        {
            _configuredModel = _appSettings.LlmModel;
            _defaultProviderModels.Add(_appSettings.LlmModel.Trim());
        }
        // 已持久化的「默认提供商分组」选择（无档案）：补入候选，重启后即使不在默认模型种子也能还原
        if (string.IsNullOrWhiteSpace(_appSettings.LastChatProfileId)
            && !string.IsNullOrWhiteSpace(_appSettings.LastChatModel))
        {
            _defaultProviderModels.Add(_appSettings.LastChatModel.Trim());
        }

        // 模型选择器：首项「默认模型」伪值 + 默认提供商分组 + 各启用服务商的全部模型
        // Key 已在 App.LoadSettings 解密为明文；停用的服务商不出现在点选列表
        // 还原上次对话页选择的模型（持久化字段）；无记录时默认选中首项「默认模型」
        // （不再按 ActiveProfileId 高亮档案模型——用户明确要求默认选中默认项）
        _isInitializingModelChoice = true;
        try
        {
            RebuildModelChoices();
            var lastProfileId = _appSettings.LastChatProfileId;
            var lastModel = _appSettings.LastChatModel;
            SelectedModelChoice = ModelChoices.FirstOrDefault(c =>
                    c.Provider?.Id == lastProfileId && c.Model == lastModel)
                ?? ModelChoices.FirstOrDefault(c => c.Provider is null && c.Model == lastModel)
                ?? ModelChoices.FirstOrDefault(c =>
                    c.Provider is { } twin && IsSameEndpointAsDefault(twin) && c.Model == lastModel)
                ?? ModelChoices.FirstOrDefault();
        }
        finally
        {
            _isInitializingModelChoice = false;
        }

        // 恢复上次勾选的「联网搜索」状态（持久化字段，避免每次启动重新勾选）
        _isWebSearchEnabled = _appSettings.EnableWebSearch;

        Messages.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(ShowEmptyGuide));
            OnPropertyChanged(nameof(EmptyGuideText));
            OnPropertyChanged(nameof(MessagesCountText));
            UpdateMessageFlags();
        };

        // 构造时拉取已有知识库集合 + 历史会话 + 后端当前模型（模型下拉种子）
        _ = LoadCollectionsAsync();
        _ = LoadSessionsAsync();
        _ = SeedModelFromConfigAsync();

        // 服务商配置变更订阅已上移至 MainViewModel（静态事件由页面容器统一管理），
        // 设置页新增/更新/删除/启停服务商后 Main 会调用 ApplyProviderConfigChanged
    }

    /// <summary>设置页服务商配置变更回调（由 MainViewModel 订阅静态事件转调）：同步默认提供商配置后重建对话页模型候选（首项默认模型名 + 各启用服务商模型）。</summary>
    public void ApplyProviderConfigChanged()
    {
        // 设置页保存后同步默认提供商（provider/model，含清空），让首项「默认 · xx」显示最新默认模型
        _configuredProvider = string.IsNullOrWhiteSpace(_appSettings.LlmProvider) ? "none" : _appSettings.LlmProvider;
        _configuredModel = _appSettings.LlmModel ?? "";
        OnPropertyChanged(nameof(IsLlmConfigured));
        OnPropertyChanged(nameof(EmptyGuideText));
        RebuildModelChoices();
    }

    /// <summary>重建模型选择器候选：首项「默认 · 默认模型名」伪值（无默认模型时显示「默认（设置页配置）」）
    /// + 默认提供商分组（按请求覆盖 model，不带 ProviderConfig；与默认端点相同的启用档案已列出的模型不再重复出现）
    /// + 各启用服务商的全部模型（「模型名（服务商名）」，携带该服务商配置按请求生效）。
    /// 设置页变更服务商/模型后调用（任务 #6 事件驱动），保证对话页立即看到最新模型。</summary>
    public void RebuildModelChoices()
    {
        var currentId = SelectedModelChoice?.Provider?.Id;
        var currentModel = SelectedModelChoice?.Model;
        ModelChoices.Clear();
        // 首项：真实默认模型名（设置页 LlmModel / 后端配置），无配置时回退到通用标签
        var defaultModelName = string.IsNullOrWhiteSpace(_configuredModel) ? null : _configuredModel.Trim();
        ModelChoices.Add(new ModelChoice(
            defaultModelName is null ? DefaultProfileLabel : $"默认 · {defaultModelName}",
            null,
            null));
        // 默认提供商分组：显示裸模型名，发送时只带 model 参数覆盖（用设置页的 provider/key/地址）；
        // 与默认端点相同的启用档案在下方已带「（服务商名）」列出同名模型，这里跳过，避免同一端点同模型出现两条
        var twinModels = CollectSameEndpointProfileModels();
        foreach (var m in _defaultProviderModels
            .Where(m => !string.IsNullOrWhiteSpace(m))
            .Select(m => m.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Where(m => !twinModels.Contains(m)))
        {
            ModelChoices.Add(new ModelChoice(m, null, m));
        }
        if (_appSettings.LlmProfiles is { Count: > 0 })
        {
            foreach (var p in _appSettings.LlmProfiles.Where(p => p is not null && p.IsEnabled))
            {
                var models = (p.Models ?? new List<string>())
                    .Where(m => !string.IsNullOrWhiteSpace(m))
                    .Select(m => m.Trim())
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .ToList();
                // 默认模型不在列表时补一个，保证能点选到默认模型
                if (!string.IsNullOrWhiteSpace(p.Model)
                    && !models.Any(m => string.Equals(m, p.Model, StringComparison.OrdinalIgnoreCase)))
                {
                    models.Add(p.Model.Trim());
                }
                foreach (var m in models)
                {
                    ModelChoices.Add(new ModelChoice($"{m}（{p.Name}）", p, m));
                }
            }
        }
        // 恢复选中：优先同服务商同模型，其次默认组同模型（被去重时落到同端点档案的孪生条目），否则默认项
        if (currentId is not null)
        {
            SelectedModelChoice = ModelChoices.FirstOrDefault(c =>
                    c.Provider?.Id == currentId && c.Model == currentModel)
                ?? ModelChoices.FirstOrDefault(c => c.Provider?.Id == currentId)
                ?? ModelChoices.FirstOrDefault();
        }
        else if (!string.IsNullOrWhiteSpace(currentModel) && currentModel != DefaultProfileLabel)
        {
            SelectedModelChoice = ModelChoices.FirstOrDefault(c =>
                    c.IsDefault == false && c.Provider is null && c.Model == currentModel)
                ?? ModelChoices.FirstOrDefault(c =>
                    c.IsDefault == false && c.Provider is { } twin
                    && IsSameEndpointAsDefault(twin) && c.Model == currentModel)
                ?? ModelChoices.FirstOrDefault();
        }
        else
        {
            SelectedModelChoice = ModelChoices.FirstOrDefault();
        }
    }

    /// <summary>收集与设置页默认配置指向同一端点的启用档案的全部模型（含各档案默认模型）。
    /// 这些模型在服务商分组里已有「模型名（服务商名）」条目，默认提供商分组不再以裸名重复列出。</summary>
    private HashSet<string> CollectSameEndpointProfileModels()
    {
        var result = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var p in _appSettings.LlmProfiles ?? new List<LlmProfile>())
        {
            if (p is not { IsEnabled: true } || !IsSameEndpointAsDefault(p))
            {
                continue;
            }
            foreach (var m in (p.Models ?? new List<string>()).Append(p.Model))
            {
                if (!string.IsNullOrWhiteSpace(m))
                {
                    result.Add(m.Trim());
                }
            }
        }
        return result;
    }

    /// <summary>档案端点与设置页默认配置是否一致：BaseUrl 归一化（去空白/结尾斜杠，忽略大小写）后比较；
    /// 两边都未填 BaseUrl 时按 provider 类型判断（同为官方默认端点）。</summary>
    private bool IsSameEndpointAsDefault(LlmProfile profile)
    {
        var profileUrl = NormalizeEndpoint(profile.BaseUrl);
        var defaultUrl = NormalizeEndpoint(_appSettings.LlmBaseUrl);
        if (profileUrl.Length > 0 || defaultUrl.Length > 0)
        {
            return string.Equals(profileUrl, defaultUrl, StringComparison.OrdinalIgnoreCase);
        }
        return string.Equals(profile.Provider, _appSettings.LlmProvider, StringComparison.OrdinalIgnoreCase);
    }

    private static string NormalizeEndpoint(string? url) => (url ?? "").Trim().TrimEnd('/');

    /// <summary>可勾选的知识库集合列表（复选框）。</summary>
    public ObservableCollection<CollectionItem> Collections { get; }

    /// <summary>历史会话列表（持久化在后端 SQLite，重启可恢复）。</summary>
    public ObservableCollection<ChatSessionItem> Sessions { get; }

    /// <summary>当前选中模型的显示名；由 SelectedModelChoice 单一事实源派生，避免双轨状态不同步。
    /// DefaultModelLabel = 用设置页配置（请求不带 model）。</summary>
    public string SelectedModel =>
        SelectedModelChoice is { IsDefault: false, Model: { } m } && !string.IsNullOrWhiteSpace(m)
            ? m
            : DefaultModelLabel;

    /// <summary>下一条消息实际会用的提供商：选了某服务商模型 → 该服务商名；否则后端全局配置。</summary>
    public string EffectiveProvider =>
        SelectedModelChoice?.Provider is { } p
            ? p.Name
            : string.IsNullOrWhiteSpace(_configuredProvider) || _configuredProvider == "none"
                ? "未配置 LLM"
                : _configuredProvider;

    /// <summary>下一条消息实际会使用的模型，区分默认配置与对话页临时覆盖。</summary>
    public string EffectiveModel =>
        SelectedModel == DefaultModelLabel
            ? (string.IsNullOrWhiteSpace(_configuredModel) ? "未配置模型" : _configuredModel)
            : SelectedModel;

    public string EffectiveModelSummary =>
        $"实际生效: {EffectiveProvider} / {EffectiveModel}" +
        (SelectedModel == DefaultModelLabel ? "（设置页默认）" : "（对话页选择）");

    private ChatSessionItem? _selectedSession;

    /// <summary>当前会话；null = 新会话（未发送过消息）。选中即载入历史消息并可续聊。</summary>
    public ChatSessionItem? SelectedSession
    {
        get => _selectedSession;
        set
        {
            if (SetProperty(ref _selectedSession, value) && value is not null && !_isLoadingSessions)
            {
                // 切换会话前必须先取消进行中的流式请求：否则旧流收尾时会用旧会话 id
                // 覆盖 _chatId 并重载会话列表，把用户刚选中的会话顶掉。
                if (_cts is { IsCancellationRequested: false })
                {
                    try { _cts.Cancel(); } catch { /* 取消失败不阻塞切换 */ }
                }
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
            // 勾选/取消即落盘，重启后恢复（与联网搜索开关同机制）
            PersistCollectionSelection();
        }
    }

    /// <summary>构造期间/刷新期间抑制勾选落盘（避免重建列表的中间状态反复写盘）。</summary>
    private bool _isRestoringCollections;

    /// <summary>把对话页勾选的知识库集合落盘（AppSettings.LastChatCollections），重启后恢复。
    /// 加载/恢复期间（_isRestoringCollections）跳过，由加载流程结束后统一落一次终态。</summary>
    private void PersistCollectionSelection()
    {
        if (_isRestoringCollections)
        {
            return;
        }
        _appSettings.LastChatCollections = SelectedCollections.ToList();
        try
        {
            _appSettings.Save();
        }
        catch
        {
            // 落盘失败不阻断对话
        }
    }

    /// <summary>是否仍需用落盘的勾选（AppSettings.LastChatCollections）恢复一次选中状态。
    /// 仅首次加载生效，之后的刷新只保留本会话内的运行时勾选（避免覆盖用户当次的选择）。</summary>
    private bool _collectionsSeedPending = true;

    private async Task LoadCollectionsAsync()
    {
        _isRestoringCollections = true;
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

            // 首次加载：用上次落盘的勾选恢复（重启后保持同样的知识库选择）
            if (_collectionsSeedPending)
            {
                _collectionsSeedPending = false;
                foreach (var saved in _appSettings.LastChatCollections)
                {
                    if (!string.IsNullOrWhiteSpace(saved))
                    {
                        selectedNames.Add(saved);
                    }
                }
                // 只保留后端仍存在的集合：落盘的集合全部被删除时退回默认勾选 default
                selectedNames.IntersectWith(names);
            }

            Collections.Clear();
            var list = names.ToList();
            list.Sort(StringComparer.OrdinalIgnoreCase);
            foreach (var name in list)
            {
                // 已有勾选则恢复；无任何勾选时（首次加载且无落盘记录）默认选 default
                var isSelected = selectedNames.Contains(name)
                    || (selectedNames.Count == 0 && string.Equals(name, "default", StringComparison.OrdinalIgnoreCase));
                Collections.Add(new CollectionItem { Name = name, IsSelected = isSelected });
            }

            OnPropertyChanged(nameof(HasSelectedCollection));
            SendCommand.NotifyCanExecuteChanged();
            DebugLog.Info($"集合列表加载完成: {list.Count} 个 [{string.Join(", ", list)}]", "Chat");
            // 终态统一落盘一次（幂等）；放在成功路径，加载失败不吞掉用户已有落盘记录
            PersistCollectionSelection();
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
        finally
        {
            _isRestoringCollections = false;
        }
    }

    private async Task AddCollectionAsync(string? name)
    {
        var trimmed = (name ?? NewCollectionName).Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return;
        }

        _isRestoringCollections = true;
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
        finally
        {
            _isRestoringCollections = false;
        }

        NewCollectionName = string.Empty;
        OnPropertyChanged(nameof(HasSelectedCollection));
        SendCommand.NotifyCanExecuteChanged();
        // 新建集合已勾选 → 落盘终态，重启后同样恢复
        PersistCollectionSelection();
    }

    /// <summary>对话消息列表。</summary>
    public ObservableCollection<ChatMessage> Messages { get; } = [];

    /// <summary>是否有消息。</summary>
    public bool HasMessages => Messages.Count > 0;

    /// <summary>无消息时显示空态。</summary>
    public bool ShowEmptyGuide => !IsBusy && Messages.Count == 0;

    /// <summary>当前会话消息数（工具条展示，如「💬 8 条消息」；无消息返回空串隐藏）。</summary>
    public string MessagesCountText =>
        Messages.Count == 0 ? string.Empty : $"💬 {Messages.Count} 条消息";

    /// <summary>LLM 是否已可用：对话页选中了某服务商档案的模型（发送时按请求携带 ProviderConfig，
    /// 不依赖全局配置），或设置页全局 provider 已配置（非 none）。
    /// 空态引导与发送前事前拦截共用此判断。</summary>
    public bool IsLlmConfigured =>
        SelectedModelChoice?.Provider is not null
        || (!string.IsNullOrWhiteSpace(_configuredProvider) && _configuredProvider != "none");

    /// <summary>空态引导文案：LLM 未配置时优先引导配置（事前引导），已配置时引导导入与提问。</summary>
    public string EmptyGuideText =>
        IsLlmConfigured
            ? "开始与知识库对话。\n\n"
              + "DocMind 会检索已导入的文档，\n"
              + "结合多轮上下文生成带来源标注的回答。\n\n"
              + "还没导入文档？先到【导入】页添加文件。"
            : "尚未配置大模型，暂无法开始对话。\n\n"
              + "请到【设置 → 大模型对话】：\n"
              + "  1️⃣ 选择提供商（OpenAI 兼容 / Claude / Gemini / Ollama）\n"
              + "  2️⃣ 填写 API Key 后点「测试连接」验证\n"
              + "  3️⃣ 回到对话页即可开始提问\n\n"
              + "💡 本地离线方案：设置页选 Ollama，无需联网与 API Key";

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
        // 事前拦截：LLM 未配置时直接引导配置，不发注定失败的请求（覆盖发送/快捷提问/重新生成入口）
        if (!IsLlmConfigured)
        {
            StatusMessage = "尚未配置大模型：请到【设置 → 大模型对话】完成配置后重试";
            _notifications?.Warning("尚未配置大模型，无法开始对话：请到【设置 → 大模型对话】完成配置", "需要配置");
            return;
        }

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

        // 先建取消令牌再置 IsBusy：消除「停止按钮已可用但 _cts 尚未创建」的竞态空窗
        _cts?.Dispose();
        _cts = new CancellationTokenSource();
        var cts = _cts;

        IsBusy = true;
        ShowStopChanged();
        StatusMessage = "对话中…";
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
                    Application.Current?.Dispatcher?.InvokeAsync(() =>
                    {
                        assistantMsg.UpdateLiveThinkingDuration(curMs);
                    });
                }
            }
            catch { /* 取消时静默退出 */ }
        }, timerCts.Token);

        // 流式排查统计：token 帧数 + 首 token 延迟（TTFT）
        var tokenCount = 0;
        long firstTokenMs = -1;
        // 本条消息是否已落地过首个正文 token。
        // 不能用 IsWaitingForFirstToken / IsThinkingInProgress 代替：这两个标志会被
        // onStatus/onThinking 回调反复置 True（模型推理链、上下文溢出重试状态帧、
        // 生成结束后的 Agent 自省帧），流中途一旦复位，下一个 token 就会把已累积的
        // 正文整体覆盖，气泡里只剩该帧之后的内容（表现为「回答只剩一个标题」）。
        var firstTokenApplied = false;
        try
        {
            var selected = SelectedCollections;
            DebugLog.Info(
                $"发送消息: query='{(query.Length > 100 ? query[..100] + "…" : query)}' " +
                $"collections=[{string.Join(",", selected)}] chatId='{_chatId ?? "-"}' model='{(SelectedModel == DefaultModelLabel ? "-" : SelectedModel)}' persona='{SelectedPersona?.Id ?? "-"}' msgCount={Messages.Count} attachCount={attachments.Count}",
                "Chat");
            ChatStreamResult? final = null;
            // 正文引用角标 [n] 点击 → 打开对应来源抽屉
            assistantMsg.SourceMarkerRequested += index =>
            {
                var src = assistantMsg.Sources?.FirstOrDefault(s => s.Index == index);
                if (src is not null)
                {
                    OpenSource(src);
                }
            };
            // 创作物解析完成 → 自动导出物理文件
            assistantMsg.ArtifactParsed += (_, artifact) =>
            {
                if (!assistantMsg.IsLoading)  // 仅在流式完成时触发
                {
                    _ = AutoExportArtifactAsync(artifact);
                }
            };
            // 发送时按选中模型项构造请求：
            //  - 默认项 → 不带 ProviderConfig / Model（用后端全局配置）
            //  - 选中某服务商的模型 → 携带该服务商配置（provider/key/url/模型），按请求生效、不污染全局
            var choice = SelectedModelChoice;
            var isDefaultChoice = choice is null || choice.IsDefault;
            var choiceProvider = choice?.Provider;
            var choiceModel = isDefaultChoice ? null : (choice?.Model ?? SelectedModel);
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
                    Model = choiceModel,
                    // 点选某服务商的模型时，携带该服务商配置（provider/key/url/模型），按请求生效
                    ProviderConfig = choiceProvider is null || isDefaultChoice
                        ? null
                        : new ProviderConfig
                        {
                            Provider = choiceProvider.Provider,
                            ApiKey = choiceProvider.ApiKey,
                            BaseUrl = choiceProvider.BaseUrl,
                            Model = choiceModel,
                        },
                    Persona = SelectedPersona?.Id,
                    EnableWebSearch = IsWebSearchEnabled,
                    Attachments = attachments.Count > 0 ? attachments : null,
                    // 本机用户自己的 GitHub Token（联网搜索 GitHub 通道按请求携带，
                    // 后端不共享、不落盘为全局配置），留空 = 用匿名公开额度
                    GithubToken = string.IsNullOrWhiteSpace(_appSettings.GithubToken)
                        ? null
                        : _appSettings.GithubToken.Trim(),
                    RagMode = _appSettings.RagMode,
                },
                onToken: token =>
                {
                    if (tokenCount == 0)
                    {
                        firstTokenMs = sw.ElapsedMilliseconds;
                        try { timerCts.Cancel(); } catch { }
                    }
                    tokenCount++;

                    void ApplyToken()
                    {
                        // 只在真正的首帧做一次「重置」（首帧前 Content 是空占位）；
                        // 此后一律追加，任何 status/thinking 帧都不得再清空已累积的正文。
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
                onStatus: status =>
                {
                    void ApplyStatus()
                    {
                        // 收集为「思考过程」步骤（检索/联网搜索/生成…），供折叠区展示；
                        assistantMsg.IsThinkingInProgress = true;
                        assistantMsg.UpdateLiveThinkingDuration(sw.ElapsedMilliseconds);
                        assistantMsg.AddThinkingStep(status ?? "");
                        assistantMsg.ShowStatus = true;
                        assistantMsg.StatusText = status;
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyStatus);
                    }
                    else
                    {
                        ApplyStatus();
                    }
                },
                onThinking: thinking =>
                {
                    void ApplyThinking()
                    {
                        // 推理链增量累积到「思考过程」区（DeepSeek-R1/Qwen3 等）
                        assistantMsg.IsThinkingInProgress = true;
                        assistantMsg.UpdateLiveThinkingDuration(sw.ElapsedMilliseconds);
                        assistantMsg.AppendThinking(thinking ?? "");
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyThinking);
                    }
                    else
                    {
                        ApplyThinking();
                    }
                },
                onRestart: () =>
                {
                    void ApplyRestart()
                    {
                        // 后端精简上下文后重新生成：上一次尝试的正文是废弃半成品，
                        // 必须显式丢弃，否则新旧两次尝试会首尾相接变成重复内容
                        firstTokenApplied = false;
                        assistantMsg.Content = string.Empty;
                    }

                    if (Application.Current?.Dispatcher is { } dispatcher && !dispatcher.CheckAccess())
                    {
                        dispatcher.InvokeAsync(ApplyRestart);
                    }
                    else
                    {
                        ApplyRestart();
                    }
                },
                onDone: result =>
                {
                    final = result;
                    try { timerCts.Cancel(); } catch { }
                    void ApplyDone()
                    {
                        assistantMsg.IsLoading = false;
                        assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
                        // 优先用后端 done 帧 model_spec 确认的显示名（而非本地预测的模型 ID）
                        assistantMsg.Model = string.IsNullOrEmpty(result.ModelDisplayName)
                            ? result.Model
                            : result.ModelDisplayName;
                        assistantMsg.Provider = result.Provider;
                        assistantMsg.ElapsedMs = result.ElapsedMs;
                        assistantMsg.Sources = result.Sources;
                        // 终帧后强制重新解析 Markdown,确保最终渲染完整(不受流式节流影响)
                        assistantMsg.ForceRefreshRender();
                        // 后端自动路由的创作意图会回传实际生效人设；仅当其为创作模式且与当前
                        // 不同时，静默同步人设下拉框（不重发请求），保证下一句请求与 UI 状态一致。
                        if (!string.IsNullOrEmpty(result.Persona)
                            && CreativePersonaIds.Contains(result.Persona!)
                            && result.Persona != _selectedPersona?.Id)
                        {
                            var persona = AvailablePersonas.FirstOrDefault(x => x.Id == result.Persona);
                            if (persona is not null)
                            {
                                _selectedPersona = persona;
                                OnPropertyChanged(nameof(SelectedPersona));
                            }
                        }
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
                ct: cts.Token);

            sw.Stop();
            DebugLog.Info(
                $"流式统计: tokens={tokenCount} 首token={(firstTokenMs >= 0 ? $"{firstTokenMs}ms" : "未收到")} " +
                $"收到done帧={(final is not null ? "是" : "否")} 总耗时{sw.ElapsedMilliseconds}ms",
                "Chat");

            // 终帧：回写多轮 chat_id + 元数据（消息属性已由 onDone 回调在 UI 线程写入，
            // 此处不再重复赋值 Sources/Model/Provider/ElapsedMs）
            if (final is not null)
            {
                var isNewChat = _chatId is null && !string.IsNullOrEmpty(final.ChatId);
                _chatId = final.ChatId ?? _chatId;
                // 状态统计：token 数（流式帧计数）+ 思考耗时文案
                assistantMsg.TokenCount = tokenCount;
                if (final.ElapsedMs > 0)
                {
                    assistantMsg.ThinkingDurationText = $"用时 {final.ElapsedMs / 1000.0:F1} 秒";
                }

                StatusMessage = $"模型: {final.Model} ({final.Provider}) · 引用 {final.TotalChunks} 块 · 耗时 {final.ElapsedMs}ms";
                DebugLog.Info($"对话完成(流式): elapsed={final.ElapsedMs}ms model={final.Model} chunks={final.TotalChunks} sources={final.Sources.Count} chatId='{final.ChatId}' partial={final.Partial}", "Chat");

                // 新会话首条回答完成 → 刷新会话列表（标题/条数已生成），选中当前会话
                if (isNewChat)
                {
                    _ = LoadSessionsAsync(selectChatId: _chatId);
                }
            }
            else
            {
                DebugLog.Warn("未收到 done 终帧：多轮 chat_id 未更新、来源/模型元数据缺失（后端可能异常中断流）", "Chat");
                StatusMessage = "回答生成失败 · 连接中断";
                // 流被对端关闭但没给终帧：同样收尾思考链路，避免「思考中/正在生成…」悬挂
                assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
                assistantMsg.FailThinkingStep("连接中断，未收到完成帧");
                // 流中断时恢复输入框，避免用户丢失原始问题
                if (tokenCount > 0)
                {
                    // 已收到部分 token 但终帧丢失：保留已生成内容，追加重试引导
                    assistantMsg.Content += $"\n\n> ⚠️ 回答因连接中断未完成，以上为已生成的部分内容。";
                    assistantMsg.Content += $"\n> 可点击「重新生成」获取完整回答。";
                }
                else
                {
                    assistantMsg.Content = "❌ 回答生成失败：与模型服务的连接中断，未收到完成帧。";
                    assistantMsg.Content += $"\n\n💡 可能原因：";
                    assistantMsg.Content += $"\n1. 模型服务暂时不可用或响应超时";
                    assistantMsg.Content += $"\n2. 网络连接不稳定";
                    assistantMsg.Content += $"\n3. 上下文过长（多个知识库 + 联网搜索结果拼接）导致超时";
                    assistantMsg.Content += $"\n\n建议：点击「重新生成」重试，或减少勾选的知识库数量后重试。";
                }
                // 恢复原始问题到输入框，方便用户修改后重试
                InputText = query;
            }
            assistantMsg.IsLoading = false;
            // 后端声明的部分回答（网络中断/用户停止等）：追加警示说明，与正常完成区分
            if (final is not null && final.Partial && !string.IsNullOrEmpty(final.Warning))
            {
                assistantMsg.Content += $"\n\n> ⚠️ {final.Warning}";
            }
            UpdateMessageFlags();
        }
        catch (OperationCanceledException)
        {
            sw.Stop();
            StatusMessage = "已停止生成";
            DebugLog.Info("对话已停止", "Chat");
            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
            assistantMsg.FailThinkingStep(null, "已停止生成");
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
            // 收尾思考链路：锁定耗时、关闭阶段胶囊、末尾「正在…」改为失败标记，
            // 避免错误提示旁仍显示「正在生成回答…」
            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
            assistantMsg.FailThinkingStep(ex.Message);
            if (string.IsNullOrWhiteSpace(assistantMsg.Content))
            {
                // 首字都没收到：整条替换为错误提示
                assistantMsg.Content = hint is null
                    ? $"❌ API 错误：{ex.Message}"
                    : $"❌ {hint}";
            }
            else
            {
                // 流式中途断开（网络抖动/后端重试耗尽）：保留已生成的部分回答，
                // 追加简洁的中断说明，让用户能复制已得内容或点「重新生成」续写。
                assistantMsg.Content += $"\n\n> ⚠️ 回答中断：{ex.Message}\n> 已生成内容已保留，可点击「重新生成」继续。";
            }
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
            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
            assistantMsg.FailThinkingStep(ex.Message);
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
            assistantMsg.CompleteThinking(sw.ElapsedMilliseconds);
            assistantMsg.FailThinkingStep(ex.Message);
            assistantMsg.Content = $"❌ 错误：{ex.Message}";
            InputText = query;
        }
        finally
        {
            timerCts.Cancel();
            timerCts.Dispose();
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

    /// <summary>撤回用户消息并回填到输入框。
    /// 支持任意位置的用户消息：移除该消息及其后续所有消息（含对应的 AI 回答），
    /// 回填到输入框供修改后重新发送。后续消息会作为新的轮次附加到现有对话中（保留多轮上下文）。</summary>
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
            // 默认撤回最后一条用户消息
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
        var removedCount = Messages.Count - userIdx;
        // 移除该用户消息及其后的所有消息（如对应的 AI 回答）
        while (Messages.Count > userIdx)
        {
            Messages.RemoveAt(Messages.Count - 1);
        }

        InputText = content;
        StatusMessage = removedCount > 2
            ? $"已撤回第 {userIdx / 2 + 1} 轮对话（含 {removedCount - 1} 条后续消息）并回填至输入框"
            : "已撤回消息并回填至输入框";
        DebugLog.Info($"已撤回用户消息: idx={userIdx} removed={removedCount} content='{(content.Length > 50 ? content[..50] + "…" : content)}'", "Chat");
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
                var msg = new ChatMessage
                {
                    Role = m.Role,
                    Content = m.Content,
                    Sources = m.Sources,
                };
                // 历史消息正文里的 [n] 角标同样可点击打开来源
                msg.SourceMarkerRequested += index =>
                {
                    var src = msg.Sources?.FirstOrDefault(s => s.Index == index);
                    if (src is not null)
                    {
                        OpenSource(src);
                    }
                };
                Messages.Add(msg);
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

            /// <summary>是否存在可预览的当前幻灯片页（无则预览卡显示空状态提示）。</summary>
            public bool HasSelectedSlide => SelectedSlide != null;

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
    public string SelectedSourceSnippet
    {
        get
        {
            var src = SelectedSource;
            if (src is null)
            {
                return "";
            }
            // 网页来源：正文没抓到且搜索摘要为空（占位/纯链接摘要已在后端过滤）时，
            // 给出诚实提示并引导打开原文，而不是展示一段像链接一样的垃圾文本。
            if (src.IsWebSource && !src.ContentFetched && string.IsNullOrWhiteSpace(src.Snippet))
            {
                return "⚠️ 未抓到该网页正文（页面可能需要 JS 渲染、需登录或禁止爬取）。搜索摘要不可用，请点击「🌐 在浏览器中打开」查看原文。";
            }
            return !string.IsNullOrWhiteSpace(src.Snippet)
                ? src.Snippet
                : "（该切片暂无全文预览或来自早期版本会话，可通过下方动作查看原文）";
        }
    }

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

    /// <summary>自动导出创作物为物理文件（后台静默执行，不阻塞 UI）。</summary>
    private async Task AutoExportArtifactAsync(ArtifactItem artifact)
    {
        try
        {
            // 延迟 500ms 等待流式完成渲染
            await Task.Delay(500);

            var fmt = artifact.Type;
            var req = new CreativeExportRequest
            {
                Content = artifact.RawContent,
                Format = fmt,
                Title = artifact.Title,
                Theme = artifact.Theme ?? "tech_blue",
            };

            var res = await _apiService.ExportCreativeArtifactAsync(req);
            if (res.Ok && !string.IsNullOrWhiteSpace(res.FilePath))
            {
                StatusMessage = $"✅ 已自动导出 {fmt.ToUpperInvariant()}：{res.FileName}";
                DebugLog.Info($"自动导出创作物成功: path={res.FilePath} size={res.FileSizeBytes}", "Chat");

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
                    DebugLog.Warn($"在资源管理器中定位自动导出文件异常: {ex.Message}", "Chat");
                }
            }
            else
            {
                DebugLog.Warn($"自动导出创作物失败: {res.Error}", "Chat");
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"自动导出创作物异常（不影响对话）: {ex.Message}", "Chat");
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
        StatusMessage = $"查看切片出处：{src.DisplayTitle} ({src.ScoreBadgeText})";
        DebugLog.Info($"展开引用来源抽屉: index={src.Index} source={src.Source} page={src.Page}", "Chat");
    }

    /// <summary>在浏览器中打开当前选中的网页来源 URL（仅 http/https，防危险协议）。</summary>
    [RelayCommand]
    private void OpenWebSource()
    {
        if (SelectedSource?.Url is not { Length: > 0 } url)
        {
            return;
        }
        if (!TryOpenHttpUrl(url))
        {
            _notifications?.Warning("该来源不是有效的网页地址（仅支持 http/https）", "无法打开");
        }
    }

    /// <summary>一键直达外部网页来源（在默认浏览器中打开）。</summary>
    [RelayCommand]
    private void OpenDirectWeb(string? url)
    {
        if (string.IsNullOrWhiteSpace(url)) return;
        if (!TryOpenHttpUrl(url))
        {
            _notifications?.Warning("该来源不是有效的网页地址（仅支持 http/https）", "无法打开");
        }
    }

    /// <summary>在默认浏览器中打开 http/https 链接；其他协议一律拒绝，返回 false。</summary>
    public static bool TryOpenHttpUrl(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
        {
            return false;
        }
        try
        {
            System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo(uri.AbsoluteUri)
                {
                    UseShellExecute = true,
                });
            return true;
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"打开网页失败: {ex.Message}", "Chat");
            return false;
        }
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
        sb.AppendLine($"- **使用模型**：{EffectiveModel}");
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
                        sb.AppendLine($"- [{s.Index}] `{s.Source}` ({s.ScoreBadgeText})");
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

            // 拉取结果并入「默认提供商」分组（保留种子模型）；选中项由 RebuildModelChoices 保留，不改全局配置
            _defaultProviderModels.Clear();
            if (!string.IsNullOrWhiteSpace(_configuredModel))
            {
                _defaultProviderModels.Add(_configuredModel);
            }
            // 持久化的「默认提供商分组」选择保留在候选池：拉取结果不含它时也不会丢失选择
            if (string.IsNullOrWhiteSpace(_appSettings.LastChatProfileId)
                && !string.IsNullOrWhiteSpace(_appSettings.LastChatModel))
            {
                _defaultProviderModels.Add(_appSettings.LastChatModel.Trim());
            }
            foreach (var m in result.Models)
            {
                if (!string.IsNullOrWhiteSpace(m))
                {
                    _defaultProviderModels.Add(m);
                }
            }
            if (!string.IsNullOrWhiteSpace(result.Provider) && result.Provider != _configuredProvider)
            {
                _configuredProvider = result.Provider;
                OnPropertyChanged(nameof(EffectiveProvider));
                OnPropertyChanged(nameof(EffectiveModelSummary));
                OnPropertyChanged(nameof(IsLlmConfigured));
                OnPropertyChanged(nameof(EmptyGuideText));
            }
            RebuildModelChoices();
            StatusMessage = $"✅ 获取到 {result.Models.Count} 个模型（{result.Provider}）";
            DebugLog.Info($"对话页模型列表: provider={result.Provider} count={result.Models.Count}", "Chat");
        }
        catch (Exception ex)
        {
            StatusMessage = $"❌ 获取模型列表失败: {ex.Message}";
            DebugLog.Warn($"对话页获取模型列表异常: {ex.Message}", "Chat");
        }
    }

    /// <summary>种子默认提供商候选：从后端配置取 llm_provider/llm_model（未拉列表前至少能看到配置值）。
    /// 同步后统一重建候选：首项「默认 · xx」显示名与默认提供商分组都会随后端配置更新。</summary>
    private async Task SeedModelFromConfigAsync()
    {
        try
        {
            var cfg = await _apiService.GetConfigAsync();
            var model = cfg?.LlmModel;
            _configuredProvider = cfg?.LlmProvider ?? "none";
            _configuredModel = model ?? string.Empty;
            OnPropertyChanged(nameof(EffectiveProvider));
            OnPropertyChanged(nameof(EffectiveModel));
            OnPropertyChanged(nameof(EffectiveModelSummary));
            OnPropertyChanged(nameof(IsLlmConfigured));
            OnPropertyChanged(nameof(EmptyGuideText));
            if (!string.IsNullOrWhiteSpace(model)
                && !_defaultProviderModels.Any(m => string.Equals(m.Trim(), model.Trim(), StringComparison.OrdinalIgnoreCase)))
            {
                _defaultProviderModels.Add(model.Trim());
            }
            // 模型名可能变化（首项显示「默认 · xx」），统一重建候选（保留当前选中项）
            RebuildModelChoices();
        }
        catch (Exception ex)
        {
            DebugLog.Debug($"读取后端配置种子模型失败（忽略）: {ex.Message}", "Chat");
        }
    }

    /// <summary>测试用：等待会话列表加载（构造时 fire-and-forget 不可 await）。</summary>
    internal Task SessionsLoadedForTestAsync() => LoadSessionsAsync();

    /// <summary>刷新历史会话列表（后端从离线恢复在线时由 MainViewModel 调用）。
    /// 构造时的加载是 fire-and-forget，后端未就绪时会失败且无重试，必须在此补一次。</summary>
    public Task RefreshSessionsAsync() => LoadSessionsAsync();

    /// <summary>测试用：等待选中会话的消息加载完成。</summary>
    internal Task SessionLoadedForTestAsync()
        => SelectedSession is null ? Task.CompletedTask : LoadSessionMessagesAsync(SelectedSession);
}

/// <summary>对话页模型选择器选项：DisplayName 显示文本；Provider 非空 = 属于某自定义服务商的模型；两者皆空 = 「设置页默认」伪项。</summary>
public sealed record ModelChoice(string DisplayName, LlmProfile? Provider, string? Model)
{
    /// <summary>是否「设置页默认」伪项（用后端全局配置，请求不携带 providerConfig）。</summary>
    public bool IsDefault => Provider is null && Model is null;

    public override string ToString() => DisplayName;
}

/// <summary>待发送附件项。</summary>
public sealed record AttachmentItem(string FullPath, string FileName, string Icon, string FileSizeText);
