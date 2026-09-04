namespace DocMind.Models;

/// <summary>
/// 用户记忆条目：存储在 SQLite FTS5 中的持久化记忆。
/// 灵感来源：Hermes Agent 的 MEMORY.md + USER.md 双层记忆。
/// </summary>
public sealed class MemoryEntry
{
    /// <summary>唯一 ID（自增）。</summary>
    public long Id { get; set; }

    /// <summary>记忆类别：memory（环境/经验）| user（用户偏好）。</summary>
    public string Category { get; set; } = "memory";

    /// <summary>记忆内容（紧凑、信息密集的自然语言）。</summary>
    public string Content { get; set; } = string.Empty;

    /// <summary>创建时间。</summary>
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    /// <summary>最后更新时间。</summary>
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    /// <summary>来源：auto（自动提取）| manual（手动添加）| feedback（反馈提取）。</summary>
    public string Source { get; set; } = "auto";
}

/// <summary>
/// 记忆搜索结果：FTS5 搜索返回的结果。
/// </summary>
public sealed class MemorySearchResult
{
    /// <summary>匹配的记忆条目。</summary>
    public MemoryEntry Entry { get; set; } = new();

    /// <summary>FTS5 排名分数（越小越相关）。</summary>
    public double Rank { get; set; }
}

/// <summary>
/// 会话搜索结果：从历史对话中 FTS5 搜索返回的结果。
/// </summary>
public sealed class SessionSearchResult
{
    /// <summary>消息内容。</summary>
    public string Content { get; set; } = string.Empty;

    /// <summary>消息角色（user / assistant）。</summary>
    public string Role { get; set; } = string.Empty;

    /// <summary>消息时间戳。</summary>
    public DateTime Timestamp { get; set; }

    /// <summary>所属会话 ID。</summary>
    public string SessionId { get; set; } = string.Empty;

    /// <summary>会话标题（首条用户问题前 50 字）。</summary>
    public string SessionTitle { get; set; } = string.Empty;

    /// <summary>FTS5 排名分数。</summary>
    public double Rank { get; set; }
}

/// <summary>
/// 记忆统计信息。
/// </summary>
public sealed class MemoryStats
{
    /// <summary>当前使用的字符数。</summary>
    public int UsedChars { get; set; }

    /// <summary>字符容量上限。</summary>
    public int MaxChars { get; set; } = 2200;

    /// <summary>使用百分比。</summary>
    public double UsagePercent => MaxChars > 0 ? (double)UsedChars / MaxChars * 100 : 0;

    /// <summary>记忆条目数。</summary>
    public int EntryCount { get; set; }

    /// <summary>是否接近容量上限（>80%）。</summary>
    public bool IsNearLimit => UsagePercent > 80;
}
