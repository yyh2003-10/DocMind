using System;
using System.ComponentModel;
using System.Text.RegularExpressions;
using CommunityToolkit.Mvvm.Input;

namespace DocMind.ViewModels;

/// <summary>思考步骤 pill 的形态：进行中 / 完成 / 警告 / 失败。</summary>
public enum ThinkingStepKind
{
    Running,
    Done,
    Warn,
    Fail,
}

/// <summary>
/// 低价值步骤（无变化/无提取/映射失败等）默认视觉降噪：
/// 短摘要 + 可展开详情，不挤占主时间线。
/// </summary>
public static class ThinkingStepNoise
{
    private static readonly string[] NoiseMarkers =
    {
        "无变化", "无提取", "无关联", "未变化", "无更新",
        "映射不可用", "结果不可用", "未配置", "已跳过", "跳过 0",
        "强制完成", "完整性评分", "无历史",
    };

    private static readonly string[] DegradedMarkers =
    {
        "映射不可用", "结果不可用", "LLM 不可用", "规划降级", "权限请求", "permission denied",
    };

    public static bool IsNoise(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return false;
        }
        foreach (var m in NoiseMarkers)
        {
            if (text.Contains(m, StringComparison.Ordinal))
            {
                return true;
            }
        }
        return false;
    }

    public static bool IsDegraded(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return false;
        }
        foreach (var m in DegradedMarkers)
        {
            if (text.Contains(m, StringComparison.Ordinal))
            {
                return true;
            }
        }
        return false;
    }
}

/// <summary>
/// 结构化思考步骤（GLM 风格紧凑 pill）：后端「正在…/✔/⚠/✖」文本协议解析为
/// 图标 + 短摘要 + 可展开详情；解析失败的行退化为普通文本（Summary 全文），不丢信息。
/// </summary>
public sealed class ThinkingStep : INotifyPropertyChanged
{
    private bool _isExpanded;

    public ThinkingStep()
    {
        ToggleCommand = new RelayCommand(() =>
        {
            if (HasDetail)
            {
                IsExpanded = !IsExpanded;
            }
        });
    }

    /// <summary>点击 pill 切换详情展开/收起（无详情时 no-op）。</summary>
    public IRelayCommand ToggleCommand { get; }

    public event PropertyChangedEventHandler? PropertyChanged;

    public ThinkingStepKind Kind { get; init; }

    /// <summary>阶段图标（📎/🔗/📚/🛡️/🌐/🔎/✍️/⚠️/❌/🧠）。</summary>
    public string Icon { get; init; } = "🧠";

    /// <summary>pill 短文案（如「5 个来源」「实体关系 · 2 条」「正在检索知识库...」）。</summary>
    public string Summary { get; init; } = "";

    /// <summary>完整详情（文件名列表、降级原因等）；点击 pill 展开。</summary>
    public string Detail { get; init; } = "";

    /// <summary>是否有可展开的详情（详情与摘要不同才算有）。</summary>
    public bool HasDetail => !string.IsNullOrWhiteSpace(Detail) && Detail != Summary;

    /// <summary>详情是否展开（点击 pill 切换）。</summary>
    public bool IsExpanded
    {
        get => _isExpanded;
        set
        {
            if (_isExpanded != value)
            {
                _isExpanded = value;
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsExpanded)));
            }
        }
    }

    /// <summary>是否进行中（pill 显示进行时样式）。</summary>
    public bool IsRunning => Kind == ThinkingStepKind.Running;

    /// <summary>是否警告/失败（pill 显示警示色）。</summary>
    public bool IsBadged => Kind is ThinkingStepKind.Warn or ThinkingStepKind.Fail;

    /// <summary>低价值步骤（无变化/无提取等）：UI 可降低不透明度，默认不展开详情。</summary>
    public bool IsNoise { get; init; }

    /// <summary>能力降级（LLM 映射/结果不可用）：警示色 + 短摘要，详情可展开。</summary>
    public bool IsDegraded { get; init; }

    /// <summary>把后端状态行解析为结构化 pill。</summary>
    public static ThinkingStep Parse(string raw)
    {
        var text = raw?.Trim() ?? "";
        if (text.Length == 0)
        {
            return new ThinkingStep { Summary = "" };
        }
        if (text.StartsWith("✖", StringComparison.Ordinal))
        {
            // 失败/中断收尾节点（FailThinkingStep 写入）：文案已截短，直接作摘要
            return new ThinkingStep
            {
                Kind = ThinkingStepKind.Fail,
                Icon = "❌",
                Summary = text[1..].Trim(),
            };
        }
        if (text.StartsWith("⚠", StringComparison.Ordinal))
        {
            var rest = text[1..].Trim();
            if (rest.Contains("回答模式", StringComparison.Ordinal)
                && (rest.Contains("回落", StringComparison.Ordinal)
                    || rest.Contains("未开启", StringComparison.Ordinal)))
            {
                return new ThinkingStep
                {
                    Kind = ThinkingStepKind.Warn,
                    Icon = "⚠️",
                    Summary = "模式 · 回落 RAG",
                    Detail = rest,
                    IsDegraded = true,
                };
            }
            var degraded = ThinkingStepNoise.IsDegraded(rest);
            return new ThinkingStep
            {
                Kind = ThinkingStepKind.Warn,
                Icon = degraded ? "🔌" : "⚠️",
                // 普通告警保留全文摘要；仅 LLM 映射/结果类降级才收成短句
                Summary = degraded ? CompactDegraded(rest) : rest,
                Detail = rest,
                IsDegraded = degraded,
                IsNoise = ThinkingStepNoise.IsNoise(rest) && !degraded,
            };
        }
        if (text.StartsWith("✔", StringComparison.Ordinal))
        {
            return ParseDone(text[1..].Trim());
        }
        if (text.StartsWith("正在", StringComparison.Ordinal))
        {
            return new ThinkingStep
            {
                Kind = ThinkingStepKind.Running,
                Icon = IconFor(text),
                Summary = text,
            };
        }
        if (text.Contains("调用工具", StringComparison.Ordinal)
            && text.Contains("web_search", StringComparison.Ordinal))
        {
            return new ThinkingStep { Kind = ThinkingStepKind.Done, Summary = "工具 · 联网搜索", Detail = text };
        }
        if (text.Contains("调用工具", StringComparison.Ordinal)
            && text.Contains("kb_search", StringComparison.Ordinal))
        {
            return new ThinkingStep { Kind = ThinkingStepKind.Done, Summary = "工具 · 知识库", Detail = text };
        }
        if (text.Contains("Agent 规划", StringComparison.Ordinal))
        {
            return new ThinkingStep { Kind = ThinkingStepKind.Done, Summary = "Agent 规划", Detail = text };
        }
        // 无法识别的行：整行显示，不丢信息（Kind 用 Done，避免误挂进行中样式）
        var rawNoise = ThinkingStepNoise.IsNoise(text);
        var rawDegraded = ThinkingStepNoise.IsDegraded(text);
        return new ThinkingStep
        {
            Kind = rawDegraded ? ThinkingStepKind.Warn : ThinkingStepKind.Done,
            Icon = rawDegraded ? "🔌" : IconFor(text),
            Summary = rawDegraded ? CompactDegraded(text) : text,
            Detail = text,
            IsDegraded = rawDegraded,
            IsNoise = rawNoise && !rawDegraded,
        };
    }

    /// <summary>降级类步骤收成短摘要，避免「已使用或映射（LLM 映射不可用）」占满时间线。</summary>
    private static string CompactDegraded(string text)
    {
        if (text.Contains("LLM 映射不可用", StringComparison.Ordinal)
            || text.Contains("映射不可用", StringComparison.Ordinal))
        {
            return "LLM 映射不可用";
        }
        if (text.Contains("LLM 结果不可用", StringComparison.Ordinal)
            || text.Contains("结果不可用", StringComparison.Ordinal))
        {
            return "LLM 结果不可用";
        }
        if (text.Contains("规划降级", StringComparison.Ordinal))
        {
            return "规划降级";
        }
        var t = text.Trim();
        return t.Length <= 24 ? t : t[..24] + "…";
    }

    /// <summary>解析「✔ 标签：详情」完成态行，收敛为紧凑摘要 + 完整详情。</summary>
    private static ThinkingStep ParseDone(string rest)
    {
        var sep = rest.IndexOf('：');
        if (sep < 0)
        {
            sep = rest.IndexOf(':');
        }
        var label = sep >= 0 ? rest[..sep].Trim() : rest.Trim();
        var detail = sep >= 0 ? rest[(sep + 1)..].Trim() : "";

        var icon = IconFor(label);
        var toolText = $"{label} {detail}";
        var isToolInvocation = label.Contains("调用工具", StringComparison.Ordinal);
        if (isToolInvocation && toolText.Contains("web_search", StringComparison.Ordinal))
        {
            return new ThinkingStep
            {
                Kind = ThinkingStepKind.Done,
                Icon = icon,
                Summary = "工具 · 联网搜索",
                Detail = detail.Length > 0 ? detail : rest,
            };
        }
        if (isToolInvocation && toolText.Contains("kb_search", StringComparison.Ordinal))
        {
            return new ThinkingStep
            {
                Kind = ThinkingStepKind.Done,
                Icon = icon,
                Summary = "工具 · 知识库",
                Detail = detail.Length > 0 ? detail : rest,
            };
        }
        var summary = label switch
        {
            var l when l.Contains("附件") => WithCount("已读取附件", rest, "个"),
            var l when l.Contains("实体关系") => WithCount("实体关系", rest, "条", none: "实体关系 · 无关联"),
            var l when l.Contains("检索知识库") => LibraryPill(rest),
            var l when l.Contains("回答策略") => rest.Contains("deep_qa") ? "定义题结构化" : label,
            var l when l.Contains("避坑") => WithCount("避坑", rest, "条", none: "避坑 · 无历史"),
            var l when l.Contains("证据偏弱") || l.Contains("补搜") => "联网 · 补搜",
            var l when l.Contains("联网") => rest.Contains("无需") || rest.Contains("未检索到")
                ? "联网 · 无结果"
                : "联网搜索",
            var l when l.Contains("回答模式") => rest.Contains("Agent")
                ? "模式 · Agent"
                : rest.Contains("回落") || rest.Contains("未开启")
                    ? "模式 · 回落 RAG"
                    : "模式 · RAG",
            var l when l.Contains("Agent 规划") => "Agent 规划",
            var l when l.Contains("调用工具") => toolText.Contains("web_search")
                ? "工具 · 联网搜索"
                : toolText.Contains("kb_search")
                    ? "工具 · 知识库"
                    : "调用工具",
            var l when l.Contains("工具结果") => "工具结果",
            var l when l.Contains("查询扩展") => "查询扩展",
            var l when l.Contains("生成") => "生成回答",
            var l when l.Contains("映射") && rest.Contains("不可用") => "LLM 映射不可用",
            var l when l.Contains("内存") || l.Contains("关系图") || l.Contains("图谱")
                => rest.Contains("无变化") || rest.Contains("无提取") || rest.Contains("无关联")
                    ? $"{label.Trim()} · 无变化"
                    : label.Trim(),
            _ => label,
        };
        var doneNoise = ThinkingStepNoise.IsNoise(rest) || ThinkingStepNoise.IsNoise(summary);
        var doneDegraded = ThinkingStepNoise.IsDegraded(rest) || ThinkingStepNoise.IsDegraded(summary);
        return new ThinkingStep
        {
            Kind = doneDegraded ? ThinkingStepKind.Warn : ThinkingStepKind.Done,
            Icon = doneDegraded ? "🔌" : icon,
            Summary = doneDegraded && summary.Contains("不可用") ? CompactDegraded(rest) : summary,
            Detail = detail.Length > 0 ? detail : rest,
            IsDegraded = doneDegraded,
            IsNoise = doneNoise && !doneDegraded,
        };
    }

    /// <summary>
    /// 库内检索 pill：优先展示「命中 N · 可引用 M」，避免「N 个来源」掩盖门控后 0 引用。
    /// </summary>
    private static string LibraryPill(string rest)
    {
        if (rest.Contains("未命中"))
        {
            return "库内 · 未命中";
        }
        var hitM = Regex.Match(rest, @"命中\s*(\d+)");
        var citeM = Regex.Match(rest, @"可引用\s*(\d+)");
        if (hitM.Success && citeM.Success)
        {
            var hit = hitM.Groups[1].Value;
            var cite = citeM.Groups[1].Value;
            return cite == "0"
                ? $"库内命中 {hit} · 可引用 0"
                : $"库内命中 {hit} · 可引用 {cite}";
        }
        return WithCount("库内", rest, "个分块", unit: "个来源");
    }

    /// <summary>从文本里抓「N 个分块 / N 条 / N 个」数量；抓不到回退默认短语。</summary>
    private static string WithCount(string prefix, string text, string countUnit, string unit = "", string? none = null)
    {
        var m = Regex.Match(text, @"(\d+)\s*" + Regex.Escape(countUnit));
        if (!m.Success)
        {
            // 兼容「(2 个)」「找到 2 条」等表述
            m = Regex.Match(text, @"(\d+)\s*个|(\d+)\s*条");
            if (!m.Success)
            {
                return none ?? prefix;
            }
        }
        var n = m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value;
        return $"{prefix} · {n} {(!string.IsNullOrEmpty(unit) ? unit : countUnit)}";
    }

    private static string IconFor(string text)
    {
        if (text.Contains("附件", StringComparison.OrdinalIgnoreCase))
        {
            return "📎";
        }
        if (text.Contains("实体关系", StringComparison.OrdinalIgnoreCase))
        {
            return "🔗";
        }
        if (text.Contains("避坑", StringComparison.OrdinalIgnoreCase))
        {
            return "🛡️";
        }
        if (text.Contains("联网", StringComparison.OrdinalIgnoreCase))
        {
            return "🌐";
        }
        if (text.Contains("检索", StringComparison.OrdinalIgnoreCase)
            || text.Contains("知识库", StringComparison.OrdinalIgnoreCase))
        {
            return "📚";
        }
        if (text.Contains("扩展", StringComparison.OrdinalIgnoreCase))
        {
            return "🔎";
        }
        if (text.Contains("生成", StringComparison.OrdinalIgnoreCase))
        {
            return "✍️";
        }
        return "🧠";
    }
}
