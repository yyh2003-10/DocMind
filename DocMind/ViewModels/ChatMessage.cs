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
    private bool _showContinueWriting;
    private bool _truncated;
    private bool _partial;
    private string? _promptTrack;
    private string? _truncatedHint;
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

    /// <summary>段落进入 bullets 的长度阈值；与 Python PARAGRAPH_MAX_LEN 对齐。</summary>
    private const int ParagraphMaxLen = 140;

    private static readonly System.Collections.Generic.Dictionary<string, string> LatexCmdMap = new()
    {
        ["alpha"] = "α", ["beta"] = "β", ["gamma"] = "γ", ["delta"] = "δ", ["Delta"] = "Δ",
        ["epsilon"] = "ε", ["theta"] = "θ", ["lambda"] = "λ", ["mu"] = "μ", ["pi"] = "π",
        ["sigma"] = "σ", ["phi"] = "φ", ["omega"] = "ω",
        ["le"] = "≤", ["ge"] = "≥", ["neq"] = "≠", ["approx"] = "≈",
        ["times"] = "×", ["cdot"] = "·", ["pm"] = "±",
        ["infty"] = "∞", ["sum"] = "∑", ["int"] = "∫", ["sqrt"] = "√",
        ["leftarrow"] = "←", ["rightarrow"] = "→", ["uparrow"] = "↑", ["downarrow"] = "↓",
    };

    private static readonly System.Text.RegularExpressions.Regex MathInlineRx =
        new(@"\$([^$\n]{1,80})\$", System.Text.RegularExpressions.RegexOptions.Compiled);
    private static readonly System.Text.RegularExpressions.Regex TableAlignRx =
        new(@"^\|?[\s\-:|]+\|?$", System.Text.RegularExpressions.RegexOptions.Compiled);
    private static readonly System.Text.RegularExpressions.Regex LatexCmdRx =
        new(@"\\([a-zA-Z]+)", System.Text.RegularExpressions.RegexOptions.Compiled);

    /// <summary>清洗单元格/正文中的 Markdown 残留与简易 $公式$（与 Python clean_markdown_inline 对齐）。</summary>
    internal static string CleanMarkdownInline(string? text)
    {
        if (string.IsNullOrEmpty(text)) return string.Empty;
        var t = MathInlineRx.Replace(text!, m =>
        {
            var inner = m.Groups[1].Value.Trim();
            inner = LatexCmdRx.Replace(inner, cm => LatexCmdMap.TryGetValue(cm.Groups[1].Value, out var u) ? u : cm.Groups[1].Value);
            inner = System.Text.RegularExpressions.Regex.Replace(inner, @"\^\{([^}]+)\}", cm => ToSuperscript(cm.Groups[1].Value));
            inner = System.Text.RegularExpressions.Regex.Replace(inner, @"\^(\w+)", cm => ToSuperscript(cm.Groups[1].Value));
            inner = System.Text.RegularExpressions.Regex.Replace(inner, @"_\{([^}]+)\}", cm => ToSubscript(cm.Groups[1].Value));
            inner = System.Text.RegularExpressions.Regex.Replace(inner, @"_(\w+)", cm => ToSubscript(cm.Groups[1].Value));
            inner = inner.Replace("{", "").Replace("}", "").Replace("\\", "");
            return inner.Trim();
        });
        t = System.Text.RegularExpressions.Regex.Replace(t, @"\*\*(.+?)\*\*", "$1");
        t = System.Text.RegularExpressions.Regex.Replace(t, @"__(.+?)__", "$1");
        t = System.Text.RegularExpressions.Regex.Replace(t, @"(?<!\*)\*([^*]+)\*(?!\*)", "$1");
        t = System.Text.RegularExpressions.Regex.Replace(t, @"(?<!_)_([^_]+)_(?!_)", "$1");
        t = System.Text.RegularExpressions.Regex.Replace(t, @"`([^`]+)`", "$1");
        return t.Trim();
    }

    private static string ToSuperscript(string s)
    {
        var map = new[] { '⁰', '¹', '²', '³', '⁴', '⁵', '⁶', '⁷', '⁸', '⁹' };
        var chars = s.ToCharArray();
        for (var i = 0; i < chars.Length; i++)
        {
            if (chars[i] is >= '0' and <= '9') chars[i] = map[chars[i] - '0'];
        }
        return new string(chars);
    }

    private static string ToSubscript(string s)
    {
        var map = new[] { '₀', '₁', '₂', '₃', '₄', '₅', '₆', '₇', '₈', '₉' };
        var chars = s.ToCharArray();
        for (var i = 0; i < chars.Length; i++)
        {
            if (chars[i] is >= '0' and <= '9') chars[i] = map[chars[i] - '0'];
        }
        return new string(chars);
    }

    /// <summary>超长段落按句读拆成多条要点，禁止静默丢弃（与 Python split_long_paragraph 对齐）。</summary>
    internal static List<string> SplitLongParagraph(string? text, int maxLen = ParagraphMaxLen)
    {
        var raw = (text ?? string.Empty).Trim();
        if (raw.Length == 0) return new List<string>();
        if (raw.Length <= maxLen) return new List<string> { raw };

        var parts = System.Text.RegularExpressions.Regex.Split(raw, @"(?<=[。；;！!？?])")
            .Select(p => p.Trim())
            .Where(p => p.Length > 0)
            .ToList();
        if (parts.Count <= 1)
        {
            var hard = new List<string>();
            for (var i = 0; i < raw.Length; i += maxLen)
            {
                hard.Add(raw.Substring(i, Math.Min(maxLen, raw.Length - i)));
            }
            return hard;
        }

        var bullets = new List<string>();
        var buf = new System.Text.StringBuilder();
        foreach (var p in parts)
        {
            if (buf.Length == 0) buf.Append(p);
            else if (buf.Length + p.Length <= maxLen) buf.Append(p);
            else
            {
                bullets.Add(buf.ToString());
                buf.Clear();
                buf.Append(p);
            }
        }
        if (buf.Length > 0) bullets.Add(buf.ToString());

        var result = new List<string>();
        foreach (var b in bullets)
        {
            if (b.Length <= maxLen) result.Add(b);
            else
            {
                for (var i = 0; i < b.Length; i += maxLen)
                    result.Add(b.Substring(i, Math.Min(maxLen, b.Length - i)));
            }
        }
        return result;
    }

    private static bool TableBlockIsComplete(List<string> block)
    {
        var hasAlign = block.Any(l => TableAlignRx.IsMatch(l));
        var dataRows = block.Count(l => !TableAlignRx.IsMatch(l));
        return hasAlign && dataRows >= 1;
    }

    private static List<List<string>> ParseTableBlock(List<string> block)
    {
        var rows = new List<List<string>>();
        foreach (var tLine in block)
        {
            if (TableAlignRx.IsMatch(tLine)) continue;
            var trimmed = tLine.Trim().Trim('|');
            var cols = trimmed.Split('|').Select(c => CleanMarkdownInline(c)).ToList();
            if (cols.Any(c => !string.IsNullOrEmpty(c))) rows.Add(cols);
        }
        return rows;
    }

    /// <summary>正文引用角标匹配：[1] / [1,2] / [1、2] / 【1】。
    /// 边界仅用 ASCII 字符类（.NET 的 \w 会把中文算作单词字符，导致“见[1]”不匹配）；
    /// 前后粘着英文/数字/方括号时不转换，避免误伤代码里的下标如 arr[1]。
    /// 同时识别全角【n】——模型常输出全角引用，旧正则漏检会导致角标挂不上。</summary>
    private static readonly System.Text.RegularExpressions.Regex SourceMarkerRegex =
        new(@"(?<![A-Za-z0-9_\]])[\[【](\d{1,3}(?:[,\s、，]\d{1,3})*)[\]】](?![A-Za-z0-9_\[【])",
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

                        var sTitle = "";  // 空表示尚未识别；禁止再用「第 N 页」当展示标题
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
                        var tableBlocks = new List<List<string>>(); // 多表原始行块
                        var curTableLines = new List<string>();

                        void FlushTableBlock()
                        {
                            if (curTableLines.Count > 0)
                            {
                                tableBlocks.Add(new List<string>(curTableLines));
                                curTableLines.Clear();
                            }
                        }

                        void AddBullet(string itemText)
                        {
                            var cleaned = CleanMarkdownInline(itemText);
                            if (string.IsNullOrEmpty(cleaned)) return;
                            foreach (var piece in SplitLongParagraph(cleaned))
                            {
                                if (curCard != null) curCard.Bullets.Add(piece);
                                else sBullets.Add(piece);
                            }
                        }

                        var lineList = pClean.Split('\n').ToList();
                        for (var li = 0; li < lineList.Count; li++)
                        {
                            var ls = lineList[li].Trim();
                            if (string.IsNullOrWhiteSpace(ls)) continue;

                            if (ls.StartsWith("# ") && string.IsNullOrEmpty(sTitle))
                            {
                                sTitle = CleanMarkdownInline(ls[2..].Trim());
                            }
                            else if (System.Text.RegularExpressions.Regex.IsMatch(ls, @"^(?:第[0-9一二三四五六七八九十]+页|Slide\s*\d+)[:：]\s*") && string.IsNullOrEmpty(sTitle))
                            {
                                sTitle = CleanMarkdownInline(System.Text.RegularExpressions.Regex.Replace(ls, @"^(?:第[0-9一二三四五六七八九十]+页|Slide\s*\d+)[:：]\s*", "").Trim());
                            }
                            else if (ls.StartsWith("## "))
                            {
                                // 封面副标题；非封面禁止丢弃，降级为弱标题或要点
                                var h2 = CleanMarkdownInline(ls[3..].Trim());
                                if (string.IsNullOrEmpty(h2)) continue;
                                if (sIndex == 1 && string.IsNullOrEmpty(sSub)) sSub = h2;
                                else if (string.IsNullOrEmpty(sTitle) && h2.Length <= 40) sTitle = h2;
                                else AddBullet(h2);
                            }
                            else if (ls.StartsWith("### "))
                            {
                                if (curCard != null) sCards.Add(curCard);
                                curCard = new SlideCardItem { Title = CleanMarkdownInline(ls[4..].Trim()) };
                            }
                            else if (ls.StartsWith(">"))
                            {
                                var q = CleanMarkdownInline(ls.TrimStart('>', ' ').Trim());
                                if (!string.IsNullOrEmpty(q))
                                    sQuote = string.IsNullOrEmpty(sQuote) ? q : sQuote + "\n" + q;
                            }
                            else if (ls.Contains('|') && ls.Count(c => c == '|') >= 2)
                            {
                                var isAlign = System.Text.RegularExpressions.Regex.IsMatch(ls, @"^\|?[\s\-:|]+\|?$");
                                var nextIsAlign = li + 1 < lineList.Count
                                    && System.Text.RegularExpressions.Regex.IsMatch(lineList[li + 1].Trim(), @"^\|?[\s\-:|]+\|?$");
                                if (!isAlign && nextIsAlign && TableBlockIsComplete(curTableLines))
                                {
                                    FlushTableBlock();
                                }
                                curTableLines.Add(ls);
                            }
                            else if (ls.StartsWith("- ") || ls.StartsWith("* ") || ls.StartsWith("+ ") || ls.StartsWith("• "))
                            {
                                AddBullet(ls[2..].Trim());
                            }
                            else if (System.Text.RegularExpressions.Regex.IsMatch(ls, @"^\d+[\.、\)]\s*"))
                            {
                                AddBullet(System.Text.RegularExpressions.Regex.Replace(ls, @"^\d+[\.、\)]\s*", "").Trim());
                            }
                            else if (!ls.StartsWith("#") && !ls.StartsWith("<!--"))
                            {
                                if (curCard != null)
                                {
                                    var cleanedPara = CleanMarkdownInline(ls);
                                    if (string.IsNullOrEmpty(curCard.Content)) curCard.Content = cleanedPara;
                                    else
                                    {
                                        foreach (var piece in SplitLongParagraph(cleanedPara))
                                            curCard.Bullets.Add(piece);
                                    }
                                }
                                else
                                {
                                    AddBullet(ls);
                                }
                            }
                        }
                        FlushTableBlock();

                        if (curCard != null) sCards.Add(curCard);

                        // 表格：多表切分 + 单元格清洗；主表留本页，续表拆独立页
                        var parsedTables = new List<List<List<string>>>();
                        foreach (var block in tableBlocks)
                        {
                            var rows = ParseTableBlock(block);
                            if (rows.Count > 0) parsedTables.Add(rows);
                        }
                        if (parsedTables.Count > 0)
                        {
                            sTable = parsedTables[0];
                        }

                        // 标题兜底：禁止输出「第 N 页」；封面优先用 artifact 标题
                        if (string.IsNullOrEmpty(sTitle))
                        {
                            if (sIndex == 1 && !string.IsNullOrWhiteSpace(aTitle) && aTitle != "知识创作交付物")
                            {
                                sTitle = aTitle.Trim();
                            }
                            else
                            {
                                var shortBullet = sBullets.FirstOrDefault(b => !string.IsNullOrWhiteSpace(b) && b.Length <= 40);
                                if (!string.IsNullOrEmpty(shortBullet))
                                {
                                    sTitle = shortBullet.Trim();
                                }
                                else if (!string.IsNullOrWhiteSpace(sSub) && sSub.Length <= 40)
                                {
                                    sTitle = sSub.Trim();
                                }
                                else if (!string.IsNullOrWhiteSpace(sQuote))
                                {
                                    var firstQuote = sQuote.Split('\n')[0].Trim();
                                    sTitle = firstQuote.Length <= 40 ? firstQuote : $"内容页 {sIndex}";
                                }
                                else
                                {
                                    sTitle = $"内容页 {sIndex}";
                                }
                            }
                        }

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

                        // 同页多表：续表拆独立 TABLE 页，禁止列数硬拼
                        for (var ti = 1; ti < parsedTables.Count; ti++)
                        {
                            item.Slides.Add(new SlideItem
                            {
                                Index = sIndex,
                                Title = $"{sTitle}（续表）",
                                Layout = "table",
                                BulletPoints = new List<string>(),
                                SpeakerNotes = string.Empty,
                                TableData = parsedTables[ti],
                            });
                            sIndex++;
                        }
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
            // 对齐 ChatView 现代简约风气泡：清除默认页边距，主题字体链 + Regular 字重，
            // 避免 FlowDocument 默认回落 Calibri 或整段 Medium 的"文档报告感"。
            doc.PagePadding = new Thickness(0);
            doc.FontFamily = ChatContentFontFamily;
            doc.FontSize = 15;
            doc.FontWeight = FontWeights.Normal;
            doc.Foreground = (Brush)(System.Windows.Application.Current?.FindResource("TextPrimaryBrush")
                                     ?? Brushes.Black);
            // 注入 Markdig.Wpf 具名样式（段落/标题/代码块/行内代码/引用/表格/链接），
            // 覆盖默认的 42px 大标题与 #d3d3d3 硬编码灰底代码块（暗色主题下刺眼）。
            ApplyMarkdownThemeStyles(doc);
            // 兜底钳制：若个别标题仍超过层级上限，压回聊天可读范围
            ClampHeadings(doc);
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

    /// <summary>对话正文基础字体链（与 Theme.xaml 全局字体一致）。</summary>
    private static readonly FontFamily ChatContentFontFamily =
        new("SF Pro Display, Segoe UI Variable Display, Segoe UI, Microsoft YaHei, sans-serif");

    /// <summary>对话正文等宽字体链（代码块与行内代码）。</summary>
    private static readonly FontFamily ChatMonoFontFamily =
        new("Consolas, 'Cascadia Code', 'Segoe UI', sans-serif");

    /// <summary>最近一次注入的普通段落样式实例（静态：渲染在 UI 线程串行执行，
    /// WireSourceMarkers 仅在同一次渲染内做引用比较）。</summary>
    private static Style? _paragraphStyle;

    /// <summary>向 FlowDocument 注入 Markdig.Wpf 的具名样式（渲染器以 ComponentResourceKey
    /// 通过 SetResourceReference 动态解析，doc.Resources 即可命中），统一现代简约观感：
    /// 段落 1.6 倍行距、小步长 SemiBold 标题、主题化代码块/行内代码/引用/表格、无下划线链接。
    /// 主题画刷缺失时（如无 Application 的测试环境）回退浅色系默认值。</summary>
    private static void ApplyMarkdownThemeStyles(FlowDocument doc)
    {
        var codeBg = FindThemeBrush("ChatCodeBlockBrush") ?? new SolidColorBrush(Color.FromRgb(0xF6, 0xF6, 0xF8));
        var codeBorder = FindThemeBrush("ChatCodeBlockBorderBrush") ?? new SolidColorBrush(Color.FromRgb(0xE5, 0xE5, 0xEA));
        var codeInlineBg = FindThemeBrush("ChatCodeInlineBrush") ?? new SolidColorBrush(Color.FromRgb(0xEF, 0xEF, 0xF2));
        var quoteFg = FindThemeBrush("ChatQuoteBrush") ?? new SolidColorBrush(Color.FromRgb(0x8E, 0x8E, 0x93));
        var linkFg = FindThemeBrush("LinkBrush") ?? new SolidColorBrush(Color.FromRgb(0x00, 0x7A, 0xFF));

        var res = doc.Resources;

        // 普通段落：段后留白 + 1.6 倍行距，让长回答有呼吸感
        var paragraph = new Style(typeof(Paragraph));
        paragraph.Setters.Add(new Setter(Block.MarginProperty, new Thickness(0, 0, 0, 8)));
        paragraph.Setters.Add(new Setter(Paragraph.LineHeightProperty, 24d));
        res[Markdig.Wpf.Styles.ParagraphStyleKey] = paragraph;
        // 保存实例引用：WireSourceMarkers 以引用比较放行普通正文段落（代码块/标题样式仍跳过）
        _paragraphStyle = paragraph;

        // 标题层级：小步长 + SemiBold，H4 起与正文同级（不再覆盖前景色，继承主题正文色）
        res[Markdig.Wpf.Styles.Heading1StyleKey] = HeadingStyle(new Thickness(0, 6, 0, 6), 17);
        res[Markdig.Wpf.Styles.Heading2StyleKey] = HeadingStyle(new Thickness(0, 6, 0, 5), 16);
        res[Markdig.Wpf.Styles.Heading3StyleKey] = HeadingStyle(new Thickness(0, 5, 0, 4), 15.5);
        res[Markdig.Wpf.Styles.Heading4StyleKey] = HeadingStyle(new Thickness(0, 4, 0, 4), 15);
        res[Markdig.Wpf.Styles.Heading5StyleKey] = HeadingStyle(new Thickness(0, 4, 0, 4), 15);
        res[Markdig.Wpf.Styles.Heading6StyleKey] = HeadingStyle(new Thickness(0, 4, 0, 4), 15);

        // 行内代码：浅底胶囊 + 等宽字体
        var inlineCode = new Style(typeof(Run));
        inlineCode.Setters.Add(new Setter(TextElement.BackgroundProperty, codeInlineBg));
        inlineCode.Setters.Add(new Setter(TextElement.FontFamilyProperty, ChatMonoFontFamily));
        res[Markdig.Wpf.Styles.CodeStyleKey] = inlineCode;

        // 代码块：主题浅底 + 等宽字体 + 内边距（替代默认硬编码 #d3d3d3）
        var codeBlock = new Style(typeof(Paragraph));
        codeBlock.Setters.Add(new Setter(Paragraph.BackgroundProperty, codeBg));
        codeBlock.Setters.Add(new Setter(Paragraph.FontFamilyProperty, ChatMonoFontFamily));
        codeBlock.Setters.Add(new Setter(Paragraph.LineHeightProperty, 21d));
        codeBlock.Setters.Add(new Setter(Block.MarginProperty, new Thickness(0, 2, 0, 8)));
        codeBlock.Setters.Add(new Setter(Paragraph.PaddingProperty, new Thickness(10, 8, 10, 8)));
        res[Markdig.Wpf.Styles.CodeBlockStyleKey] = codeBlock;

        // 引用块：左侧细竖线 + 次级文字色（替代默认 4px 灰墙）
        var quote = new Style(typeof(Section));
        quote.Setters.Add(new Setter(Section.BorderBrushProperty, codeBorder));
        quote.Setters.Add(new Setter(Section.BorderThicknessProperty, new Thickness(2.5, 0, 0, 0)));
        quote.Setters.Add(new Setter(Section.ForegroundProperty, quoteFg));
        quote.Setters.Add(new Setter(Section.PaddingProperty, new Thickness(10, 2, 0, 2)));
        res[Markdig.Wpf.Styles.QuoteBlockStyleKey] = quote;

        // 表格：无竖线的现代表格——水平细线 + 表头加粗浅底
        var table = new Style(typeof(Table));
        table.Setters.Add(new Setter(Table.CellSpacingProperty, 0d));
        res[Markdig.Wpf.Styles.TableStyleKey] = table;

        var cell = new Style(typeof(TableCell));
        cell.Setters.Add(new Setter(TableCell.BorderBrushProperty, codeBorder));
        cell.Setters.Add(new Setter(TableCell.BorderThicknessProperty, new Thickness(0, 0, 0, 1)));
        cell.Setters.Add(new Setter(TableCell.PaddingProperty, new Thickness(8, 5, 8, 5)));
        res[Markdig.Wpf.Styles.TableCellStyleKey] = cell;

        var headerRow = new Style(typeof(TableRow));
        headerRow.Setters.Add(new Setter(TableRow.BackgroundProperty, codeBg));
        headerRow.Setters.Add(new Setter(TextElement.FontWeightProperty, FontWeights.SemiBold));
        res[Markdig.Wpf.Styles.TableHeaderStyleKey] = headerRow;

        // 超链接：主题链接色、默认无下划线、悬浮显示下划线
        var link = new Style(typeof(Hyperlink));
        link.Setters.Add(new Setter(Hyperlink.ForegroundProperty, linkFg));
        link.Setters.Add(new Setter(TextBlock.TextDecorationsProperty, null));
        var hover = new Trigger { Property = Hyperlink.IsMouseOverProperty, Value = true };
        hover.Setters.Add(new Setter(TextBlock.TextDecorationsProperty, System.Windows.TextDecorations.Underline));
        link.Triggers.Add(hover);
        res[Markdig.Wpf.Styles.HyperlinkStyleKey] = link;
    }

    private static Style HeadingStyle(Thickness margin, double fontSize)
    {
        var style = new Style(typeof(Paragraph));
        style.Setters.Add(new Setter(Block.MarginProperty, margin));
        style.Setters.Add(new Setter(Paragraph.FontSizeProperty, fontSize));
        style.Setters.Add(new Setter(TextElement.FontWeightProperty, FontWeights.SemiBold));
        return style;
    }

    private static Brush? FindThemeBrush(string key)
        => System.Windows.Application.Current?.TryFindResource(key) as Brush;

    /// <summary>兜底钳制：Heading 样式已把标题压到 17px；若个别段落仍被赋予更大的直设字号
    /// （旧样式路径或渲染器直写），统一压回 15.5px SemiBold，并清掉标题默认的下划线装饰。</summary>
    private static void ClampHeadings(FlowDocument doc)
    {
        foreach (var block in doc.Blocks)
        {
            ClampHeadings(block);
        }
    }

    private static void ClampHeadings(Block block)
    {
        switch (block)
        {
            case Paragraph p:
                // Heading 样式已把标题压到 17px；此处仅兜底钳制更大的直设字号并清除下划线装饰
                if (p.FontSize > 17)
                {
                    p.FontSize = 15.5;
                    p.FontWeight = FontWeights.SemiBold;
                }
                if (p.TextDecorations is { Count: > 0 })
                {
                    p.TextDecorations = null;
                }
                break;
            case List list:
                foreach (var item in list.ListItems)
                {
                    foreach (var itemBlock in item.Blocks)
                    {
                        ClampHeadings(itemBlock);
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
                                ClampHeadings(cellBlock);
                            }
                        }
                    }
                }
                break;
            case Section section:
                foreach (var child in section.Blocks)
                {
                    ClampHeadings(child);
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
        // 仅跳过代码块等非正文段落。旧逻辑用 Style 引用比较：Markdig 在无
        // Application 的测试环境或未套用自定义 Style 时，会把全部正文段落
        // 误判为非正文 → 引用角标挂不上。
        if (IsLikelyCodeParagraph(paragraph))
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

    private static bool IsLikelyCodeParagraph(Paragraph paragraph)
    {
        static bool Mono(FontFamily? ff)
        {
            if (ff is null) return false;
            var name = ff.Source ?? "";
            return name.Contains("Consolas", StringComparison.OrdinalIgnoreCase)
                   || name.Contains("Cascadia", StringComparison.OrdinalIgnoreCase)
                   || name.Contains("Courier", StringComparison.OrdinalIgnoreCase)
                   || name.Contains("Mono", StringComparison.OrdinalIgnoreCase);
        }

        if (Mono(paragraph.FontFamily)) return true;
        if (paragraph.Tag is string tag && tag.Contains("code", StringComparison.OrdinalIgnoreCase)) return true;
        if (paragraph.Style is Style style)
        {
            foreach (var setter in style.Setters.OfType<Setter>())
            {
                if (setter.Property == TextElement.FontFamilyProperty && setter.Value is FontFamily ff && Mono(ff))
                {
                    return true;
                }
            }
        }
        return false;
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

            var isPdfSource = !targetSource.IsWebSource && (string.Equals(targetSource.Format, "pdf", StringComparison.OrdinalIgnoreCase) || targetSource.Source.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase));

            var tipHint = new TextBlock
            {
                Text = isWeb 
                    ? "💡 点击直接在默认浏览器中打开该网页（同时展示来源抽屉）" 
                    : (isPdfSource 
                        ? $"🎯 点击展开原著阅读器并定位高亮 (第 P{targetSource.Page ?? 1} 页)" 
                        : "💡 点击直接在右侧抽屉查证切片原文"),
                FontSize = 10,
                Foreground = isPdfSource 
                    ? (Brush)(System.Windows.Application.Current?.FindResource("PrimaryBrush") ?? Brushes.DodgerBlue)
                    : (Brush)(System.Windows.Application.Current?.FindResource("TextTertiaryBrush") ?? Brushes.Gray),
                FontWeight = isPdfSource ? FontWeights.SemiBold : FontWeights.Normal,
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
            // 防御过滤：未精读网页不得出现在列表（后端已保证；历史脏数据兜底）
            IReadOnlyList<SourceRef>? cleaned = value;
            if (value is not null)
            {
                var filtered = value.Where(s => !(s.IsWebSource && !s.ContentFetched)).ToList();
                cleaned = filtered.Count == value.Count ? value : filtered;
            }
            if (SetField(ref _sources, cleaned))
            {
                foreach (var name in new[]
                {
                    nameof(HasSources), nameof(WebSourceCount), nameof(WebFetchedCount),
                    nameof(HasWebSources), nameof(SearchSummaryText),
                    nameof(WebSources), nameof(LocalSources),
                    nameof(TokenStatText), nameof(HasTokenStat),
                    nameof(EvidenceSummaryText), nameof(HasEvidenceWarning),
                    nameof(HasEvidenceBar), nameof(ShowSourcesList),
                    nameof(EvidenceExpandHint),
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

    private EvidenceSummary? _evidence;

    /// <summary>后端 done 帧证据摘要；历史会话可为 null，此时从 Sources 兜底推算。</summary>
    public EvidenceSummary? Evidence
    {
        get => _evidence;
        set
        {
            if (SetField(ref _evidence, value))
            {
                foreach (var name in new[]
                {
                    nameof(EvidenceSummaryText), nameof(HasEvidenceWarning),
                    nameof(HasEvidenceBar),
                })
                {
                    PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(name));
                }
            }
        }
    }

    /// <summary>当前生效的证据摘要（后端字段优先，旧数据从 Sources 推算）。</summary>
    private EvidenceSummary EffectiveEvidence =>
        Evidence ?? EvidenceSummary.FromSources(Sources);

    /// <summary>检索到了来源但答案零实质引用（citation_audit.evidence_support 为空）。
    /// 此时不允许证据条暗示「引用了 N 篇」——正文很可能已明说资料与主题无关。</summary>
    public bool HasSourcesButZeroCited
    {
        get
        {
            if (EffectiveEvidence.FallbackGeneralKnowledge || !HasSources)
            {
                return false;
            }
            var audit = EffectiveEvidence.CitationAudit;
            if (audit is null)
            {
                return false;
            }
            return audit.EvidenceSupport.Count == 0;
        }
    }

    /// <summary>证据条文案，例如「库内原文 5 · 精读网页 1 · 图谱边」或通用知识警告。</summary>
    public string EvidenceSummaryText
    {
        get
        {
            var ev = EffectiveEvidence;
            if (ev.FallbackGeneralKnowledge)
            {
                return "本地未命中 · 基于通用知识";
            }
            // 零实质引用：诚实标注「检索到但未作引用」，不伪装成已引用
            if (HasSourcesButZeroCited)
            {
                var zeroParts = new List<string>();
                if (ev.LocalCount > 0)
                {
                    zeroParts.Add($"库内检索 {ev.LocalCount} 条");
                }
                if (ev.WebFetchedCount > 0)
                {
                    zeroParts.Add($"精读网页 {ev.WebFetchedCount} 篇");
                }
                if (zeroParts.Count == 0)
                {
                    return "检索到资料 · 未作引用";
                }
                zeroParts.Add("未作引用");
                return string.Join(" · ", zeroParts);
            }
            var parts = new List<string>();
            if (ev.LocalCount > 0)
            {
                parts.Add($"库内原文 {ev.LocalCount}");
            }
            if (ev.WebFetchedCount > 0)
            {
                parts.Add($"精读网页 {ev.WebFetchedCount}");
            }
            if (ev.GraphInjected)
            {
                parts.Add("图谱");
            }
            if (parts.Count == 0)
            {
                if (ev.WebUnfetchedCount > 0)
                {
                    return "仅有未精读网页摘要";
                }
                return "";
            }
            return string.Join(" · ", parts);
        }
    }

    /// <summary>是否呈现通用知识警告样式（本地未命中 fallback）。</summary>
    public bool HasEvidenceWarning => EffectiveEvidence.FallbackGeneralKnowledge;

    /// <summary>是否显示证据条（有来源证据 或 通用知识警告）。</summary>
    public bool HasEvidenceBar =>
        !IsUser
        && !string.IsNullOrEmpty(EvidenceSummaryText)
        && !IsLoading
        && !IsWaitingForFirstToken;

    private bool _isSourcesExpanded = true;

    /// <summary>来源列表是否展开（证据条点击切换）。</summary>
    public bool IsSourcesExpanded
    {
        get => _isSourcesExpanded;
        set
        {
            if (SetField(ref _isSourcesExpanded, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ShowSourcesList)));
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(EvidenceExpandHint)));
            }
        }
    }

    /// <summary>来源列表是否可见：有来源且展开；零实质引用时不铺无用切片列表（仅留证据条文字交代）。</summary>
    public bool ShowSourcesList => HasSources && !HasSourcesButZeroCited && IsSourcesExpanded;

    /// <summary>点击证据条切换来源列表展开/收起（零实质引用时无可展开内容，no-op）。</summary>
    [RelayCommand]
    private void ToggleEvidenceBar()
    {
        if (!HasSources || HasSourcesButZeroCited)
        {
            return;
        }
        IsSourcesExpanded = !IsSourcesExpanded;
    }

    /// <summary>证据条右侧展开指示（有可展开来源时显示 chevron；零实质引用时不显示）。</summary>
    public string EvidenceExpandHint => HasSources && !HasSourcesButZeroCited
        ? (IsSourcesExpanded ? "收起来源" : $"展开 {Sources!.Count} 条来源")
        : "";

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
                if (!value)
                {
                    // 生成完成：自动收起思考区（2026-09-13 豆包问答截图：
                    // nemotron 英文自我编排推理链整屏泄漏，答完仍占屏）。
                    // 流式期间保持展开看进度，答完收起，「已思考」入口可再展开。
                    IsThinkingExpanded = false;
                }
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

    /// <summary>是否显示「继续写」（截断/部分回答时，由 VM 维护）。</summary>
    public bool ShowContinueWriting
    {
        get => _showContinueWriting;
        set => SetField(ref _showContinueWriting, value);
    }

    /// <summary>P0：输出 token 上限截断。</summary>
    public bool Truncated
    {
        get => _truncated;
        set => SetField(ref _truncated, value);
    }

    /// <summary>P0：后端 partial（中断/停止/截断）。</summary>
    public bool Partial
    {
        get => _partial;
        set => SetField(ref _partial, value);
    }

    /// <summary>P0：提示词轨（rag/delivery），用于调试与状态条。</summary>
    public string? PromptTrack
    {
        get => _promptTrack;
        set => SetField(ref _promptTrack, value);
    }

    /// <summary>P0：截断提示文案（独立字段，不写入 Content，避免污染续写正文）。</summary>
    public string? TruncatedHint
    {
        get => _truncatedHint;
        set => SetField(ref _truncatedHint, value);
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

    /// <summary>流式阶段收集的思考/搜索过程步骤原文（解析附件、检索知识库、联网搜索、生成…）。</summary>
    public ObservableCollection<string> ThinkingSteps { get; } = new();

    /// <summary>结构化思考步骤 pill（GLM 风格：图标 + 短摘要 + 可展开详情），由 ThinkingSteps 派生。</summary>
    public ObservableCollection<ThinkingStep> ThinkingStepPills { get; } = new();

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
            ClampHeadings(doc);
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
        // 规划帧与模型思考帧是两段独立内容，直接拼接会粘成
        // 「…知识图谱Provide detailed introduction…」。补一个换行分隔。
        if (ThinkingText.Length > 0
            && !ThinkingText.EndsWith("\n")
            && !text.StartsWith("\n"))
        {
            ThinkingText += "\n";
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
            // pill 同步收敛替换：进行中 pill → 完成 pill（GLM 风格紧凑摘要）
            if (ThinkingStepPills.Count > 0 && ThinkingStepPills[^1].IsRunning)
            {
                ThinkingStepPills[^1] = ThinkingStep.Parse(text);
            }
            else
            {
                ThinkingStepPills.Add(ThinkingStep.Parse(text));
            }
            return;
        }
        ThinkingSteps.Add(text);
        ThinkingStepPills.Add(ThinkingStep.Parse(text));
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
        var failPill = ThinkingStep.Parse(step);
        if (ThinkingSteps.Count > 0 && ThinkingSteps[^1].StartsWith("正在", StringComparison.Ordinal))
        {
            ThinkingSteps[^1] = step;
            if (ThinkingStepPills.Count > 0 && ThinkingStepPills[^1].IsRunning)
            {
                ThinkingStepPills[^1] = failPill;
            }
            else
            {
                ThinkingStepPills.Add(failPill);
            }
        }
        else if (ThinkingSteps.Count == 0 || ThinkingSteps[^1] != step)
        {
            ThinkingSteps.Add(step);
            ThinkingStepPills.Add(failPill);
        }
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasThinkingSteps)));
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(ThinkingIconText)));
        PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HasThinking)));
    }

    private int? _highlightedSourceIndex;

    /// <summary>临时高亮的来源角标 index（点击正文 [n] 后 1.5s 自动清除）。</summary>
    public int? HighlightedSourceIndex
    {
        get => _highlightedSourceIndex;
        set
        {
            if (SetField(ref _highlightedSourceIndex, value))
            {
                PropertyChanged?.Invoke(this, new System.ComponentModel.PropertyChangedEventArgs(nameof(HighlightedSourceIndex)));
            }
        }
    }

    private int _highlightVersion;

    /// <summary>触发引用角标点击：展开来源列表 + 临时高亮对应卡片 + 通知打开抽屉。</summary>
    public void NotifySourceMarker(int index)
    {
        if (HasSources)
        {
            IsSourcesExpanded = true;
        }
        HighlightedSourceIndex = index;
        var version = ++_highlightVersion;
        _ = System.Threading.Tasks.Task.Delay(1500).ContinueWith(_ =>
        {
            if (_highlightVersion == version)
            {
                if (Application.Current?.Dispatcher is { } dispatcher)
                {
                    dispatcher.InvokeAsync(() => HighlightedSourceIndex = null);
                }
                else
                {
                    HighlightedSourceIndex = null;
                }
            }
        }, System.Threading.Tasks.TaskScheduler.Default);
        SourceMarkerRequested?.Invoke(index);
    }

    /// <summary>判断指定来源是否处于高亮状态（供卡片样式绑定）。</summary>
    public bool IsSourceHighlighted(int index) => _highlightedSourceIndex == index;

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

