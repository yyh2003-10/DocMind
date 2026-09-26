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
    /// <summary>人话相关度：高/中/低/附件/排名参考/未知。前端优先展示；旧数据为空时回退 ScoreBadgeText。</summary>
    public string ConfidenceLabel { get; init; } = string.Empty;
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

    /// <summary>引用分数徽标：后端已给 confidence_label 时优先人话（高/中/低）；
    /// 旧数据无 label 时按量纲选择标签，避免把 RRF 排名分误读为「相似度」。</summary>
    public string ScoreBadgeText
    {
        get
        {
            if (!string.IsNullOrWhiteSpace(ConfidenceLabel) && ConfidenceLabel != "未知")
            {
                return ConfidenceLabel switch
                {
                    "高" => "高相关",
                    "中" => "中相关",
                    "低" => "弱相关",
                    _ => ConfidenceLabel,
                };
            }
            return ScoreType switch
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
        }
    }

    public string QualityText => Score > 0
        ? ScoreBadgeText
        : "综合质量: 未知";

    /// <summary>展示用标题：本地来源优先文件名（脱敏绝对路径），并附加页码/分块号区分同文件多切片。</summary>
    public string DisplayTitle
    {
        get
        {
            var raw = !string.IsNullOrWhiteSpace(Title) ? Title! : Source;
            var baseTitle = SanitizeDisplayPath(raw);
            if (IsWebSource)
            {
                return baseTitle;
            }
            if (Page is int page && page > 0)
            {
                return $"{baseTitle} · P{page}";
            }
            if (ChunkId is int chunkId)
            {
                return $"{baseTitle} · #{chunkId}";
            }
            return baseTitle;
        }
    }

    /// <summary>把本地绝对路径压成文件名，避免引用列表泄露完整用户目录。</summary>
    public static string SanitizeDisplayPath(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return string.Empty;
        }
        var trimmed = value.Trim();
        try
        {
            if (trimmed.Contains('\\') || trimmed.Contains('/'))
            {
                var name = System.IO.Path.GetFileName(trimmed.TrimEnd('\\', '/'));
                if (!string.IsNullOrWhiteSpace(name))
                {
                    return name;
                }
            }
        }
        catch (ArgumentException)
        {
            // 非法路径字符：原样返回，由上游截断控制展示宽度
        }
        return trimmed;
    }
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

    /// <summary>结构化证据摘要；旧后端可为 null。</summary>
    public EvidenceSummary? Evidence { get; init; }
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

    /// <summary>P0：提示词轨（rag | delivery）；旧后端可为 null。</summary>
    public string? PromptTrack { get; init; }

    /// <summary>P0：输出 token 上限导致正文被截断。</summary>
    public bool Truncated { get; init; }

    /// <summary>P0：后端支持「继续写」补全。</summary>
    public bool ContinueSupported { get; init; }

    /// <summary>P0：normal | continue。</summary>
    public string? ResponseMode { get; init; }

    /// <summary>P0：截断时的续写引导文案。</summary>
    public string? ContinueHint { get; init; }

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

    /// <summary>结构化证据摘要（done 帧 evidence）；旧后端/旧会话可为 null。</summary>
    public EvidenceSummary? Evidence { get; init; }
}

/// <summary>结构化证据摘要：前端证据条数据源，优先用后端字段，旧数据可从 Sources 兜底推算。</summary>
public sealed record EvidenceSummary
{
    public int LocalCount { get; init; }
    public int LocalCiteCount { get; init; }
    public int LocalHitCount { get; init; }
    public int WebFetchedCount { get; init; }
    public int WebUnfetchedCount { get; init; }
    public bool GraphInjected { get; init; }
    public bool FallbackGeneralKnowledge { get; init; }
    public int CitableTotal { get; init; }
    public int SynthesizedSourceCount { get; init; }
    public bool SingleSource { get; init; }
    public bool WebOnly { get; init; }
    public bool DegradedRetrieval { get; init; }
    public string? PromptTrack { get; init; }
    /// <summary>答案 [n] 引用审计；旧后端可为 null。</summary>
    public CitationAudit? CitationAudit { get; init; }

    /// <summary>旧会话/旧后端缺 evidence 字段时，从 Sources 兜底推算（不含 graph/fallback）。</summary>
    public static EvidenceSummary FromSources(IReadOnlyList<SourceRef>? sources)
    {
        if (sources is null || sources.Count == 0)
        {
            return new EvidenceSummary();
        }
        var local = sources.Count(s => !s.IsWebSource);
        var webF = sources.Count(s => s.IsWebSource && s.ContentFetched);
        var webU = sources.Count(s => s.IsWebSource && !s.ContentFetched);
        var total = local + webF + webU;
        return new EvidenceSummary
        {
            LocalCount = local,
            LocalCiteCount = local,
            LocalHitCount = local,
            WebFetchedCount = webF,
            WebUnfetchedCount = webU,
            CitableTotal = total,
            SingleSource = total <= 1,
            WebOnly = local == 0 && total > 0,
        };
    }
}

/// <summary>后端 audit_answer_citations 结果：答案引用编号是否落在有效来源内。</summary>
public sealed record CitationAudit
{
    public IReadOnlyList<int> Cited { get; init; } = [];
    public IReadOnlyList<int> Valid { get; init; } = [];
    public IReadOnlyList<int> Invalid { get; init; } = [];

    /// <summary>实际用作证据支撑的编号（排除「并未提及/未找到」类免责声明引用）。</summary>
    public IReadOnlyList<int> EvidenceSupport { get; init; } = [];

    /// <summary>仅出现在免责声明语境中的编号（答案明说未采用这些资料）。</summary>
    public IReadOnlyList<int> DisclaimerOnly { get; init; } = [];

    public double ValidRatio { get; init; } = 1.0;
    public bool Ok { get; init; } = true;

    /// <summary>人话提示；无问题时为空。</summary>
    public string? WarningText =>
        !Ok && Invalid.Count > 0
            ? $"答案引用了不存在的来源编号 [{string.Join("], [", Invalid)}]，请以左侧来源列表为准"
            : null;
}