namespace DocMind.Models;

/// <summary>对应后端 SourceRefDTO（RAG 回答引用来源）。</summary>
public sealed record SourceRef
{
    public int Index { get; init; }
    public string Source { get; init; } = string.Empty;
    /// <summary>后端分块 ID（可空，兼容旧会话数据）。用于定位到原文档中的精确分块。</summary>
    public int? ChunkId { get; init; }
    public string Format { get; init; } = string.Empty;
    public int? Page { get; init; }
    public string? Heading { get; init; }
    public double Score { get; init; }
    /// <summary>Score 的量纲类型：rerank(重排相关度)/vector(向量相似度)/bm25(关键词匹配)/
    /// rrf(RRF 融合排名分)/web_relevance(网页相关度)/attachment(附件全文)；空 = 旧数据。</summary>
    public string ScoreType { get; init; } = string.Empty;
    public string SourceType { get; init; } = "local";
    public string? Url { get; init; }
    public string? Title { get; init; }
    public string? Snippet { get; init; }
    public string? SourceName { get; init; }  // 搜索引擎来源 (DuckDuckGo / WebSearch)
    public string? Domain { get; init; }
    public string? PublishedAt { get; init; }
    public bool ContentFetched { get; init; }
    public int CorroboratedBy { get; init; }
    public string EvidenceLevel { get; init; } = "单一来源";

    public bool IsWebSource => SourceType == "web" || !string.IsNullOrWhiteSpace(Url);

    public string EvidenceBadgeText => EvidenceLevel switch
    {
        "多来源共识" => "✅ 多来源共识",
        "交叉印证" => "🔎 交叉印证",
        _ => "⚠️ 单一来源",
    };

    public string AuthorityBadgeText
    {
        get
        {
            if (string.IsNullOrWhiteSpace(Domain)) return "外部网页";
            var host = Domain.Trim().TrimStart('.');
            foreach (var authoritative in AppSettings.AuthoritativeWebDomains)
            {
                if (host.Equals(authoritative, StringComparison.OrdinalIgnoreCase)
                    || host.EndsWith("." + authoritative, StringComparison.OrdinalIgnoreCase))
                {
                    return "官方域名";
                }
            }
            return "外部网页";
        }
    }

    public string PublishedText => string.IsNullOrWhiteSpace(PublishedAt)
        ? "日期未识别"
        : $"资料日期: {PublishedAt}";

    /// <summary>引用分数徽标：按量纲选择标签，避免把 RRF 排名分（~0.02）误读为「相似度」。
    /// 旧会话数据无 ScoreType，按分值启发式回退（RRF 量纲 < 0.05）。</summary>
    public string ScoreBadgeText => ScoreType switch
    {
        "rerank" => $"相关度 {Score:F2}",
        "vector" => $"相似度 {Score:F2}",
        "bm25" => $"关键词匹配 {Score:F2}",
        "rrf" => $"排名分 {Score:F2}",
        "web_relevance" => $"网页相关度 {Score:P0}",
        "attachment" => "附件全文",
        _ when IsWebSource && Score >= 0.999 => "网页来源",
        _ when !IsWebSource && Score > 0 && Score < 0.05 => $"排名分 {Score:F2}",
        _ => $"相似度 {Score:F2}",
    };

    public string QualityText => Score > 0
        ? ScoreBadgeText
        : "综合质量: 未知";

    public string DisplayTitle => !string.IsNullOrWhiteSpace(Title) ? Title : Source;
}

/// <summary>POST /v1/chat 响应体。</summary>
public sealed record ChatResponse
{
    /// <summary>LLM 生成的回答文本。</summary>
    public string Answer { get; init; } = string.Empty;

    /// <summary>会话 ID（多轮对话时传同一值）。</summary>
    public string ChatId { get; init; } = string.Empty;

    /// <summary>使用的模型名。</summary>
    public string Model { get; init; } = string.Empty;

    /// <summary>提供商标识（openai / ollama）。</summary>
    public string Provider { get; init; } = string.Empty;

    /// <summary>引用 chunk 总数。</summary>
    public int TotalChunks { get; init; }

    /// <summary>耗时（毫秒）。</summary>
    public int ElapsedMs { get; init; }

    /// <summary>引用来源列表。</summary>
    public IReadOnlyList<SourceRef> Sources { get; init; } = [];
}

/// <summary>POST /v1/chat/stream 终帧（done=true）解析结果。</summary>
public sealed record ChatStreamResult
{
    public string ChatId { get; init; } = string.Empty;
    public string Model { get; init; } = string.Empty;
    public string Provider { get; init; } = string.Empty;

    /// <summary>后端本轮实际生效的办公/创作人设（office/ppt/doc/lesson/table/web/...）。
    /// 创作意图被后端自动路由到人设时，此值可能与前端请求传入的 persona 不同，
    /// 前端据此同步人设下拉框，保证 UI 状态与下一句请求一致。</summary>
    public string? Persona { get; init; }

    public int TotalChunks { get; init; }
    public int ElapsedMs { get; init; }
    public IReadOnlyList<SourceRef> Sources { get; init; } = [];

    /// <summary>后端声明的部分回答（网络中断/用户停止等），与正常完成区分。</summary>
    public bool Partial { get; init; }

    /// <summary>后端附带的警示说明（partial=true 时通常非空）。</summary>
    public string? Warning { get; init; }

    /// <summary>后端确认的模型显示名（done 帧 model_spec.display_name，可能是本地未收录的自定义模型名）。</summary>
    public string? ModelDisplayName { get; init; }

    /// <summary>后端确认的模型上下文窗口（token）。</summary>
    public int? ContextWindow { get; init; }

    /// <summary>后端确认的模型最大输出 token。</summary>
    public int? MaxOutputTokens { get; init; }

    /// <summary>后端确认的模型是否为深度思考（推理）模型。</summary>
    public bool? IsReasoningModel { get; init; }

    /// <summary>后端生成的模型规格摘要文案。</summary>
    public string? ModelSpecSummary { get; init; }
}