using System.Collections.ObjectModel;
using System.Text.Json.Serialization;

namespace DocMind.Models;

/// <summary>PPT 主题配色选项。</summary>
public sealed class PptThemeOption
{
    public string Id { get; }
    public string DisplayName { get; }
    public string Icon { get; }
    public string Description { get; }
    public string PrimaryHex { get; }
    public string BgHex { get; }

    public PptThemeOption(string id, string displayName, string icon, string description, string primaryHex, string bgHex)
    {
        Id = id;
        DisplayName = displayName;
        Icon = icon;
        Description = description;
        PrimaryHex = primaryHex;
        BgHex = bgHex;
    }
}

/// <summary>创作交付物导出请求。</summary>
public sealed class CreativeExportRequest
{
    [JsonPropertyName("content")]
    public string Content { get; set; } = string.Empty;

    [JsonPropertyName("format")]
    public string? Format { get; set; }

    [JsonPropertyName("outputPath")]
    public string? OutputPath { get; set; }

    [JsonPropertyName("title")]
    public string? Title { get; set; }

    [JsonPropertyName("theme")]
    public string? Theme { get; set; }
}

/// <summary>创作交付物导出响应结果。</summary>
public sealed class CreativeExportResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("format")]
    public string Format { get; set; } = string.Empty;

    [JsonPropertyName("file_path")]
    public string FilePath { get; set; } = string.Empty;

    [JsonPropertyName("file_name")]
    public string FileName { get; set; } = string.Empty;

    [JsonPropertyName("file_size_bytes")]
    public long FileSizeBytes { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

/// <summary>卡片网格中的单个卡片。</summary>
public sealed class SlideCardItem
{
    public string Title { get; set; } = string.Empty;
    public string Content { get; set; } = string.Empty;
    public List<string> Bullets { get; set; } = new();
}

/// <summary>大数字 KPI 看板中的单个指标项。</summary>
public sealed class MetricItem
{
    public string Value { get; set; } = string.Empty;
    public string Label { get; set; } = string.Empty;
    public string Description { get; set; } = string.Empty;
}

/// <summary>时间线路线图中的单个阶段节点。</summary>
public sealed class TimelineNodeItem
{
    public string Stage { get; set; } = string.Empty;
    public string Title { get; set; } = string.Empty;
}

/// <summary>单页幻灯片前端展示模型。</summary>
public sealed class SlideItem
{
    public int Index { get; set; }
    public string Title { get; set; } = string.Empty;
    public string Subtitle { get; set; } = string.Empty;
    public string Layout { get; set; } = "general"; // cover, agenda, cards, metrics, timeline, table, quote, general
    public List<string> BulletPoints { get; set; } = new();
    public string SpeakerNotes { get; set; } = string.Empty;
    public List<List<string>>? TableData { get; set; }
    public List<SlideCardItem> Cards { get; set; } = new();
    public List<MetricItem> Metrics { get; set; } = new();
    public List<TimelineNodeItem> TimelineNodes { get; set; } = new();
    public string QuoteText { get; set; } = string.Empty;

    public bool IsCover => string.Equals(Layout, "cover", StringComparison.OrdinalIgnoreCase) || Index == 1;
    public bool IsCards => string.Equals(Layout, "cards", StringComparison.OrdinalIgnoreCase) || Cards.Count > 0;
    public bool IsMetrics => string.Equals(Layout, "metrics", StringComparison.OrdinalIgnoreCase) || Metrics.Count > 0;
    public bool IsTimeline => string.Equals(Layout, "timeline", StringComparison.OrdinalIgnoreCase) || TimelineNodes.Count > 0;
    public bool IsTable => string.Equals(Layout, "table", StringComparison.OrdinalIgnoreCase) || (TableData is { Count: > 0 });
    public bool IsQuote => string.Equals(Layout, "quote", StringComparison.OrdinalIgnoreCase) || !string.IsNullOrWhiteSpace(QuoteText);
    public bool IsGeneral => !IsCover && !IsCards && !IsMetrics && !IsTimeline && !IsTable && !IsQuote;

    public bool HasNotes => !string.IsNullOrWhiteSpace(SpeakerNotes);
    public bool HasTable => TableData is { Count: > 0 };
}

/// <summary>创作交付物前端综合模型。</summary>
public sealed class ArtifactItem
{
    public string Type { get; set; } = "docx"; // pptx, docx, xlsx, html, md
    public string Title { get; set; } = "知识创作交付物";
    public string Theme { get; set; } = "tech_blue";
    public string RawContent { get; set; } = string.Empty;
    public List<SlideItem> Slides { get; set; } = new();
    public int SlideCount => Slides.Count;
    public bool IsPpt => string.Equals(Type, "pptx", StringComparison.OrdinalIgnoreCase);
    public bool IsDoc => string.Equals(Type, "docx", StringComparison.OrdinalIgnoreCase);
    public bool IsExcel => string.Equals(Type, "xlsx", StringComparison.OrdinalIgnoreCase);
    public bool IsHtml => string.Equals(Type, "html", StringComparison.OrdinalIgnoreCase);
}

/// <summary>PPT 自检问题项 DTO。</summary>
public sealed class InspectionIssueDto
{
    [JsonPropertyName("level")]
    public string Level { get; set; } = "suggestion"; // error, warning, suggestion, info

    [JsonPropertyName("category")]
    public string Category { get; set; } = string.Empty;

    [JsonPropertyName("message")]
    public string Message { get; set; } = string.Empty;

    [JsonPropertyName("slide_index")]
    public int? SlideIndex { get; set; }

    [JsonPropertyName("fix_suggestion")]
    public string FixSuggestion { get; set; } = string.Empty;

    public bool IsWarning => string.Equals(Level, "warning", StringComparison.OrdinalIgnoreCase);
    public bool IsError => string.Equals(Level, "error", StringComparison.OrdinalIgnoreCase);
    public bool IsSuggestion => string.Equals(Level, "suggestion", StringComparison.OrdinalIgnoreCase);
    public bool IsInfo => string.Equals(Level, "info", StringComparison.OrdinalIgnoreCase);
}

/// <summary>PPT 效果自检报告响应 DTO。</summary>
public sealed class PptInspectionReportDto
{
    [JsonPropertyName("score")]
    public int Score { get; set; }

    [JsonPropertyName("grade")]
    public string Grade { get; set; } = "A";

    [JsonPropertyName("summary")]
    public string Summary { get; set; } = string.Empty;

    [JsonPropertyName("slide_count")]
    public int SlideCount { get; set; }

    [JsonPropertyName("notes_coverage_pct")]
    public double NotesCoveragePct { get; set; }

    [JsonPropertyName("archetype_diversity")]
    public int ArchetypeDiversity { get; set; }

    [JsonPropertyName("total_words")]
    public int TotalWords { get; set; }

    [JsonPropertyName("avg_words_per_slide")]
    public double AvgWordsPerSlide { get; set; }

    [JsonPropertyName("issues")]
    public List<InspectionIssueDto> Issues { get; set; } = new();

    [JsonPropertyName("recommendations")]
    public List<string> Recommendations { get; set; } = new();

    [JsonPropertyName("highlights")]
    public List<string> Highlights { get; set; } = new();

    public int WarningCount => Issues.Count(i => i.IsWarning || i.IsError);
    public int SuggestionCount => Issues.Count(i => i.IsSuggestion);
}

