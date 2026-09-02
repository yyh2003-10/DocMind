namespace DocMind.Models;

/// <summary>AI 知识库整理请求（POST /v1/curate）：打标签/摘要/归类/语义去重/归纳合并。走异步 job。</summary>
public sealed record CurateRequest
{
    /// <summary>目标集合；null = 整理全部集合。</summary>
    public string? Collection { get; init; }

    /// <summary>动作列表；默认 enrich/categorize/dedup/consolidate 四项。
    /// 刻意排除 extract（实体抽取成本高且由图谱页单独触发），避免一键整理产生意外副作用。</summary>
    public List<string>? Actions { get; init; }

    /// <summary>True = 只读预览（零写入，后端默认也是 true）。
    /// dedup/consolidate 有损失，务必先预览确认，再用 false 执行。</summary>
    public bool DryRun { get; init; } = true;

    /// <summary>enrich/categorize 处理的文档数上限（1-200，LLM 调用成本护栏）。</summary>
    public int? TopK { get; init; }
}