using System.IO;
using System.Text;
using DocMind.Models;
using Microsoft.Data.Sqlite;

namespace DocMind.Services;

/// <summary>
/// 反馈服务：存储用户对 AI 回答的反馈（点赞/点踩/纠正），并自动分析模式更新记忆。
/// 灵感来源：Hermes Agent 的后台自我审查循环 + Mem0 的事实提取管线。
///
/// 设计要点：
/// - 每条助手消息可独立评价（👍/👎 + 可选纠正文本）
/// - 达到阈值后自动分析反馈模式，提取经验教训存入用户记忆
/// - 反馈数据本身也存储在 SQLite 中，支持统计和趋势分析
/// </summary>
public sealed class FeedbackService : IDisposable
{
    private readonly string _dbPath;
    private readonly object _lock = new();
    private bool _initialized;

    /// <summary>触发自动分析所需的最小反馈条数。</summary>
    public const int AutoAnalyzeThreshold = 5;

    public FeedbackService()
    {
        var configDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "DocMind");
        Directory.CreateDirectory(configDir);
        _dbPath = Path.Combine(configDir, "feedback.db");
    }

    /// <summary>用于单元测试的临时路径构造。</summary>
    internal FeedbackService(string dbPath)
    {
        _dbPath = dbPath;
    }

    private void EnsureInitialized()
    {
        if (_initialized) return;
        lock (_lock)
        {
            if (_initialized) return;
            using var conn = OpenConnection();
            InitializeSchema(conn);
            _initialized = true;
        }
    }

    private SqliteConnection OpenConnection()
    {
        var conn = new SqliteConnection($"Data Source={_dbPath}");
        conn.Open();
        Execute(conn, "PRAGMA journal_mode=WAL");
        Execute(conn, "PRAGMA busy_timeout=5000");
        return conn;
    }

    private static void InitializeSchema(SqliteConnection conn)
    {
        Execute(conn, """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                chat_id TEXT,
                rating TEXT NOT NULL CHECK(rating IN ('up', 'down')),
                correction TEXT,
                query_snapshot TEXT,
                response_snapshot TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_chat ON feedback(chat_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating);
        """);

        // 分析结果表：记录已分析的反馈批次
        Execute(conn, """
            CREATE TABLE IF NOT EXISTS feedback_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                insights TEXT NOT NULL,
                memory_entries_added INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """);
    }

    // ═══════════════════════════════════════════════════════
    //  公共 API
    // ═══════════════════════════════════════════════════════

    /// <summary>记录用户反馈。</summary>
    public Task<bool> SubmitFeedbackAsync(
        string messageId,
        string rating,
        string? correction = null,
        string? chatId = null,
        string? querySnapshot = null,
        string? responseSnapshot = null)
    {
        EnsureInitialized();
        if (string.IsNullOrWhiteSpace(messageId) || (rating != "up" && rating != "down"))
            return Task.FromResult(false);

        lock (_lock)
        {
            using var conn = OpenConnection();
            Execute(conn,
                "INSERT INTO feedback (message_id, chat_id, rating, correction, query_snapshot, response_snapshot) VALUES ($mid, $cid, $rat, $corr, $query, $resp)",
                ("$mid", messageId),
                ("$cid", chatId ?? ""),
                ("$rat", rating),
                ("$corr", correction ?? ""),
                ("$query", querySnapshot ?? ""),
                ("$resp", responseSnapshot ?? ""));

            DebugLog.Info($"反馈已记录: message={Truncate(messageId, 20)} rating={rating} correction={Truncate(correction ?? "-", 30)}", "Feedback");
        }
        return Task.FromResult(true);
    }

    /// <summary>获取指定会话的反馈列表。</summary>
    public Task<IReadOnlyList<FeedbackEntry>> GetFeedbacksAsync(string? chatId = null, int limit = 50)
    {
        EnsureInitialized();
        lock (_lock)
        {
            using var conn = OpenConnection();
            var cmd = conn.CreateCommand();
            cmd.CommandText = chatId is null
                ? "SELECT * FROM feedback ORDER BY id DESC LIMIT $limit"
                : "SELECT * FROM feedback WHERE chat_id=$cid ORDER BY id DESC LIMIT $limit";
            cmd.Parameters.AddWithValue("$limit", limit);
            if (chatId is not null) cmd.Parameters.AddWithValue("$cid", chatId);

            var entries = new List<FeedbackEntry>();
            using var reader = cmd.ExecuteReader();
            while (reader.Read())
            {
                entries.Add(new FeedbackEntry
                {
                    Id = reader.GetInt64(0),
                    MessageId = reader.GetString(1),
                    ChatId = reader.IsDBNull(2) ? "" : reader.GetString(2),
                    Rating = reader.GetString(3),
                    Correction = reader.IsDBNull(4) ? "" : reader.GetString(4),
                    QuerySnapshot = reader.IsDBNull(5) ? "" : reader.GetString(5),
                    ResponseSnapshot = reader.IsDBNull(6) ? "" : reader.GetString(6),
                    CreatedAt = DateTime.TryParse(reader.GetString(7), out var dt) ? dt : DateTime.MinValue,
                });
            }
            return Task.FromResult<IReadOnlyList<FeedbackEntry>>(entries);
        }
    }

    /// <summary>获取反馈统计。</summary>
    public Task<FeedbackStats> GetStatsAsync()
    {
        EnsureInitialized();
        lock (_lock)
        {
            using var conn = OpenConnection();
            var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT
                    COUNT(*) as total,
                    COALESCE(SUM(CASE WHEN rating='up' THEN 1 ELSE 0 END), 0) as up_count,
                    COALESCE(SUM(CASE WHEN rating='down' THEN 1 ELSE 0 END), 0) as down_count
                FROM feedback
            """;
            using var reader = cmd.ExecuteReader();
            if (reader.Read())
            {
                return Task.FromResult(new FeedbackStats
                {
                    TotalFeedbacks = reader.GetInt32(0),
                    UpCount = reader.GetInt32(1),
                    DownCount = reader.GetInt32(2),
                });
            }
            return Task.FromResult(new FeedbackStats());
        }
    }

    /// <summary>检查是否达到自动分析阈值。</summary>
    public async Task<bool> ShouldAnalyzeAsync()
    {
        var stats = await GetStatsAsync();
        return stats.TotalFeedbacks >= AutoAnalyzeThreshold;
    }

    /// <summary>
    /// 自动分析反馈模式：找出点踩最多的查询模式，提取经验教训存入记忆。
    /// 这是 Phase 4 自我改进循环的核心。
    /// </summary>
    public async Task<int> AnalyzeAndStoreInsightsAsync(UserMemoryService memoryService)
    {
        EnsureInitialized();
        var feedbacks = await GetFeedbacksAsync(limit: 100);
        if (feedbacks.Count < AutoAnalyzeThreshold) return 0;

        int insightsAdded = 0;

        // 分析点踩反馈中的纠正文本 → 提取经验教训
        var downFeedbacks = feedbacks.Where(f => f.Rating == "down" && !string.IsNullOrWhiteSpace(f.Correction)).ToList();
        foreach (var fb in downFeedbacks)
        {
            var insight = $"[反馈纠正] {fb.Correction}";
            if (await memoryService.AddAsync(insight, "memory", "feedback"))
            {
                insightsAdded++;
            }
        }

        // 分析点赞反馈 → 提取成功模式
        var upFeedbacks = feedbacks.Where(f => f.Rating == "up" && !string.IsNullOrWhiteSpace(f.QuerySnapshot)).ToList();
        if (upFeedbacks.Count >= 3)
        {
            // 取最近的点赞查询，提取共同主题
            var recentQueries = upFeedbacks.Take(5).Select(f => f.QuerySnapshot).ToList();
            var commonThemes = ExtractCommonThemes(recentQueries);
            if (!string.IsNullOrWhiteSpace(commonThemes))
            {
                var insight = $"[成功模式] 用户经常问这类问题且满意回答：{commonThemes}";
                if (await memoryService.AddAsync(insight, "memory", "feedback"))
                {
                    insightsAdded++;
                }
            }
        }

        // 记录分析结果
        if (insightsAdded > 0)
        {
            lock (_lock)
            {
                using var conn = OpenConnection();
                Execute(conn,
                    "INSERT INTO feedback_analysis (batch_id, insights, memory_entries_added) VALUES ($bid, $ins, $cnt)",
                    ("$bid", DateTime.UtcNow.ToString("yyyyMMdd_HHmmss")),
                    ("$ins", $"Analyzed {feedbacks.Count} feedbacks, added {insightsAdded} insights"),
                    ("$cnt", insightsAdded));
            }
            DebugLog.Info($"反馈分析完成: 分析 {feedbacks.Count} 条反馈，提取 {insightsAdded} 条经验", "Feedback");
        }

        return insightsAdded;
    }

    /// <summary>从多条查询中提取共同主题（简单关键词频率分析）。</summary>
    private static string ExtractCommonThemes(IReadOnlyList<string> queries)
    {
        if (queries.Count == 0) return string.Empty;

        // 简单方法：取所有查询中最常出现的名词/动词片段
        var words = new Dictionary<string, int>();
        foreach (var q in queries)
        {
            // 按标点和空格拆分
            var tokens = System.Text.RegularExpressions.Regex.Split(q, @"[\s,.;!?。，；！？、：]+");
            foreach (var t in tokens)
            {
                if (t.Length >= 2)
                {
                    words[t] = words.GetValueOrDefault(t, 0) + 1;
                }
            }
        }

        // 取出现 2 次以上的词
        var common = words.Where(kv => kv.Value >= 2)
                          .OrderByDescending(kv => kv.Value)
                          .Take(5)
                          .Select(kv => kv.Key);

        return string.Join("、", common);
    }

    private static void Execute(SqliteConnection conn, string sql, params (string name, object value)[] parameters)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        foreach (var (name, value) in parameters)
        {
            cmd.Parameters.AddWithValue(name, value);
        }
        cmd.ExecuteNonQuery();
    }

    private static string Truncate(string text, int maxLen) =>
        text.Length <= maxLen ? text : text[..maxLen] + "…";

    public void Dispose() { }
}

/// <summary>反馈条目。</summary>
public sealed class FeedbackEntry
{
    public long Id { get; set; }
    public string MessageId { get; set; } = string.Empty;
    public string ChatId { get; set; } = string.Empty;
    public string Rating { get; set; } = string.Empty; // "up" | "down"
    public string Correction { get; set; } = string.Empty;
    public string QuerySnapshot { get; set; } = string.Empty;
    public string ResponseSnapshot { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; }
}

/// <summary>反馈统计。</summary>
public sealed class FeedbackStats
{
    public int TotalFeedbacks { get; set; }
    public int UpCount { get; set; }
    public int DownCount { get; set; }
    public double SatisfactionRate => TotalFeedbacks > 0 ? (double)UpCount / TotalFeedbacks : 0;
}
