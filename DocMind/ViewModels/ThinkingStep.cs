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
            return new ThinkingStep
            {
                Kind = ThinkingStepKind.Warn,
                Icon = "⚠️",
                Summary = rest,
                Detail = rest,
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
        // 无法识别的行：整行显示，不丢信息（Kind 用 Done，避免误挂进行中样式）
        return new ThinkingStep { Kind = ThinkingStepKind.Done, Summary = text, Detail = text };
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
        var summary = label switch
        {
            var l when l.Contains("附件") => WithCount("已读取附件", rest, "个"),
            var l when l.Contains("实体关系") => WithCount("实体关系", rest, "条", none: "实体关系 · 无关联"),
            var l when l.Contains("检索知识库") => rest.Contains("未命中")
                ? "库内 · 未命中"
                : WithCount("库内", rest, "个分块", unit: "个来源"),
            var l when l.Contains("避坑") => WithCount("避坑", rest, "条", none: "避坑 · 无历史"),
            var l when l.Contains("联网") => rest.Contains("无需") || rest.Contains("未检索到")
                ? "联网 · 无结果"
                : "联网搜索",
            var l when l.Contains("查询扩展") => "查询扩展",
            var l when l.Contains("生成") => "生成回答",
            _ => label,
        };
        return new ThinkingStep
        {
            Kind = ThinkingStepKind.Done,
            Icon = icon,
            Summary = summary,
            Detail = detail.Length > 0 ? detail : rest,
        };
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
