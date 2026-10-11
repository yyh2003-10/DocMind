using System.IO;
using System.Text;
using DocMind.Models;
using Microsoft.Data.Sqlite;

namespace DocMind.Services;

/// <summary>
/// 用户持久记忆服务：SQLite 存储 + FTS5 全文搜索。
/// 灵感来源：Hermes Agent 的 MEMORY.md + USER.md 双层记忆，Mem0 的提取管线。
///
/// 设计要点：
/// - 有界容量（2,200 字符 / ~800 tokens），避免系统提示膨胀
/// - FTS5 全文搜索，~20ms 延迟，零 LLM 成本
/// - 自动去重（添加前检查内容是否已存在）
/// - 支持类别过滤（memory / user）
/// </summary>
public sealed class UserMemoryService : IDisposable
{
    private readonly string _dbPath;
    private readonly object _lock = new();
    private bool _initialized;

    /// <summary>默认容量上限（与 Hermes Agent 对齐）。</summary>
    public const int DefaultMaxChars = 2200;

    public UserMemoryService()
    {
        var configDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "DocMind");
        Directory.CreateDirectory(configDir);
        _dbPath = Path.Combine(configDir, "user_memory.db");
    }

    /// <summary>用于单元测试的临时路径构造。</summary>
    internal UserMemoryService(string dbPath)
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
        // WAL 模式 + busy_timeout：与项目后端 chat_store.py 保持一致
        Execute(conn, "PRAGMA journal_mode=WAL");
        Execute(conn, "PRAGMA busy_timeout=5000");
        return conn;
    }

    private static void InitializeSchema(SqliteConnection conn)
    {
        Execute(conn, """
            CREATE TABLE IF NOT EXISTS user_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL DEFAULT 'memory',
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_user_memory_category ON user_memory(category);
        """);

        // FTS5 虚拟表：unicode61 将 CJK 视为 token char，需在插入前 SegmentCjk 分词
        // 不使用外部内容表（content=xxx）和触发器，改为代码手动同步 FTS 索引
        Execute(conn, """
            CREATE VIRTUAL TABLE IF NOT EXISTS user_memory_fts
            USING fts5(content, category, tokenize='unicode61 remove_diacritics 2');
        """);
    }

    /// <summary>CJK 分词：在 CJK 字符之间、CJK 与非 CJK 之间插入空格，使 FTS5 simple tokenizer 能逐字分词。</summary>
    private static string SegmentCjk(string text)
    {
        // 每个 CJK 字符前后插入空格，使 unicode61 将每个 CJK 字符视为独立 token
        // "用户喜欢Python开发" → " 用 户 喜 欢 Python 开 发 "
        // 查询 "Python" OR "开" OR "发" 就能精确匹配
        var sb = new System.Text.StringBuilder(text.Length * 2);
        var cjkRun = new System.Text.StringBuilder();
        for (int i = 0; i < text.Length; i++)
        {
            var c = text[i];
            if (IsCjk(c))
            {
                sb.Append(' ');
                sb.Append(c);
                cjkRun.Append(c);
            }
            else
            {
                AppendCjkBigrams(sb, cjkRun);
                cjkRun.Clear();
                sb.Append(c);
            }
        }
        AppendCjkBigrams(sb, cjkRun);
        return sb.ToString();
    }

    private static void AppendCjkBigrams(StringBuilder target, StringBuilder run)
    {
        for (var i = 0; i + 1 < run.Length; i++)
        {
            target.Append(' ').Append(run[i]).Append(run[i + 1]);
        }
    }

    private static void FtsInsert(SqliteConnection conn, long rowid, string content, string category)
    {
        Execute(conn,
            "INSERT INTO user_memory_fts(rowid, content, category) VALUES ($id, $c, $cat)",
            ("$id", rowid), ("$c", SegmentCjk(content)), ("$cat", category));
    }

    private static void FtsDelete(SqliteConnection conn, long rowid)
    {
        Execute(conn, "DELETE FROM user_memory_fts WHERE rowid=$id", ("$id", rowid));
    }

    // ═══════════════════════════════════════════════════════
    //  公共 API
    // ═══════════════════════════════════════════════════════

    /// <summary>添加记忆条目。自动去重：内容完全相同时跳过。</summary>
    public Task<bool> AddAsync(string content, string category = "memory", string source = "auto")
    {
        EnsureInitialized();
        if (string.IsNullOrWhiteSpace(content)) return Task.FromResult(false);

        content = content.Trim();

        lock (_lock)
        {
            using var conn = OpenConnection();

            // 去重检查
            if (IsDuplicate(conn, content)) return Task.FromResult(false);

            // 容量检查
            var stats = GetStatsInternal(conn);
            if (stats.UsedChars + content.Length > stats.MaxChars)
            {
                DebugLog.Warn(
                    $"记忆容量已满（{stats.UsedChars}/{stats.MaxChars}），新条目({content.Length}字符)被拒绝",
                    "Memory");
                return Task.FromResult(false);
            }

            Execute(conn,
                "INSERT INTO user_memory (category, content, source) VALUES ($cat, $content, $src)",
                ("$cat", category), ("$content", content), ("$src", source));

            // 手动同步 FTS 索引（用 SQL 获取最后插入的 rowid）
            using var idCmd = conn.CreateCommand();
            idCmd.CommandText = "SELECT last_insert_rowid()";
            var newId = (long)idCmd.ExecuteScalar()!;
            FtsInsert(conn, newId, content, category);

            DebugLog.Info($"记忆已添加 [{category}] {Truncate(content, 60)}", "Memory");
        }
        return Task.FromResult(true);
    }

    /// <summary>替换记忆条目（子字符串匹配）。</summary>
    public Task<bool> ReplaceAsync(string oldTextSubstring, string newContent, string? category = null)
    {
        EnsureInitialized();
        if (string.IsNullOrWhiteSpace(oldTextSubstring) || string.IsNullOrWhiteSpace(newContent))
            return Task.FromResult(false);

        newContent = newContent.Trim();

        lock (_lock)
        {
            using var conn = OpenConnection();

            var cmd = conn.CreateCommand();
            cmd.CommandText = category is null
                ? "SELECT id, content FROM user_memory WHERE content LIKE $pattern LIMIT 2"
                : "SELECT id, content FROM user_memory WHERE category=$cat AND content LIKE $pattern LIMIT 2";
            cmd.Parameters.AddWithValue("$pattern", $"%{oldTextSubstring}%");
            if (category is not null) cmd.Parameters.AddWithValue("$cat", category);

            long? targetId = null;
            string? targetContent = null;
            using (var reader = cmd.ExecuteReader())
            {
                int count = 0;
                while (reader.Read())
                {
                    count++;
                    if (count == 1)
                    {
                        targetId = reader.GetInt64(0);
                        targetContent = reader.GetString(1);
                    }
                }
                if (count == 0) return Task.FromResult(false);
                if (count > 1)
                {
                    DebugLog.Warn($"记忆替换匹配到 {count} 条记录，请提供更精确的匹配文本", "Memory");
                    return Task.FromResult(false);
                }
            }

            if (targetId is null || targetContent is null) return Task.FromResult(false);

            // 容量检查：替换后长度
            var stats = GetStatsInternal(conn);
            var delta = newContent.Length - targetContent.Length;
            if (stats.UsedChars + delta > stats.MaxChars)
            {
                DebugLog.Warn($"替换后将超出容量限制({stats.UsedChars + delta}/{stats.MaxChars})", "Memory");
                return Task.FromResult(false);
            }

            // 先删旧 FTS 再插新 FTS
            FtsDelete(conn, targetId.Value);
            FtsInsert(conn, targetId.Value, newContent, category ?? "memory");

            Execute(conn,
                "UPDATE user_memory SET content=$new, updated_at=datetime('now') WHERE id=$id",
                ("$new", newContent), ("$id", targetId.Value));

            DebugLog.Info($"记忆已替换: \"{Truncate(targetContent, 40)}\" → \"{Truncate(newContent, 40)}\"", "Memory");
        }
        return Task.FromResult(true);
    }

    /// <summary>删除记忆条目（子字符串匹配）。</summary>
    /// <summary>按 ID 删除单条记忆。</summary>
    public Task<bool> DeleteByIdAsync(long id)
    {
        EnsureInitialized();
        lock (_lock)
        {
            using var conn = OpenConnection();
            FtsDelete(conn, id);
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "DELETE FROM user_memory WHERE id=$id";
            cmd.Parameters.AddWithValue("$id", id);
            return Task.FromResult(cmd.ExecuteNonQuery() > 0);
        }
    }

    public Task<bool> RemoveAsync(string textSubstring, string? category = null)
    {
        EnsureInitialized();
        if (string.IsNullOrWhiteSpace(textSubstring)) return Task.FromResult(false);

        lock (_lock)
        {
            using var conn = OpenConnection();

            var cmd = conn.CreateCommand();
            cmd.CommandText = category is null
                ? "DELETE FROM user_memory WHERE content LIKE $pattern"
                : "DELETE FROM user_memory WHERE category=$cat AND content LIKE $pattern";
            cmd.Parameters.AddWithValue("$pattern", $"%{textSubstring}%");
            if (category is not null) cmd.Parameters.AddWithValue("$cat", category);

            var affected = cmd.ExecuteNonQuery();
            if (affected > 0)
            {
                // 重建 FTS 索引（数据量小，可接受）
                Execute(conn, "DELETE FROM user_memory_fts");
                using var rebuildCmd = conn.CreateCommand();
                rebuildCmd.CommandText = "SELECT id, content, category FROM user_memory";
                using var reader = rebuildCmd.ExecuteReader();
                while (reader.Read())
                {
                    FtsInsert(conn, reader.GetInt64(0), reader.GetString(1), reader.GetString(2));
                }
                DebugLog.Info($"记忆已删除 {affected} 条（匹配: \"{Truncate(textSubstring, 40)}\"）", "Memory");
            }
            return Task.FromResult(affected > 0);
        }
    }

    /// <summary>获取全部记忆条目。</summary>
    public Task<IReadOnlyList<MemoryEntry>> GetAllAsync(string? category = null)
    {
        EnsureInitialized();
        lock (_lock)
        {
            using var conn = OpenConnection();
            var cmd = conn.CreateCommand();
            cmd.CommandText = category is null
                ? "SELECT id, category, content, source, created_at, updated_at FROM user_memory ORDER BY id"
                : "SELECT id, category, content, source, created_at, updated_at FROM user_memory WHERE category=$cat ORDER BY id";
            if (category is not null) cmd.Parameters.AddWithValue("$cat", category);

            var entries = new List<MemoryEntry>();
            using var reader = cmd.ExecuteReader();
            while (reader.Read())
            {
                entries.Add(ReadEntry(reader));
            }
            return Task.FromResult<IReadOnlyList<MemoryEntry>>(entries);
        }
    }

    /// <summary>FTS5 全文搜索记忆。</summary>
    public Task<IReadOnlyList<MemorySearchResult>> SearchAsync(string query, int limit = 10)
    {
        EnsureInitialized();
        if (string.IsNullOrWhiteSpace(query))
            return Task.FromResult<IReadOnlyList<MemorySearchResult>>(Array.Empty<MemorySearchResult>());

        lock (_lock)
        {
            using var conn = OpenConnection();

            // FTS5 查询：对中文做简单处理，用 OR 连接分词
            var ftsQuery = BuildFtsQuery(query);

            var cmd = conn.CreateCommand();
            // FTS5 MATCH 不支持参数绑定，需内联查询字符串（用单引号包裹防注入）
            var safeQuery = ftsQuery.Replace("'", "''");
            cmd.CommandText = $"""
                SELECT m.id, m.category, m.content, m.source, m.created_at, m.updated_at,
                       fts.rank
                FROM user_memory_fts fts
                JOIN user_memory m ON m.id = fts.rowid
                WHERE user_memory_fts MATCH '{safeQuery}'
                ORDER BY fts.rank
                LIMIT $limit
            """;
            cmd.Parameters.AddWithValue("$limit", limit);

            var results = new List<MemorySearchResult>();
            using var reader = cmd.ExecuteReader();
            while (reader.Read())
            {
                results.Add(new MemorySearchResult
                {
                    Entry = ReadEntry(reader),
                    Rank = reader.GetDouble(6)
                });
            }
            return Task.FromResult<IReadOnlyList<MemorySearchResult>>(results);
        }
    }

    /// <summary>获取记忆统计信息。</summary>
    public Task<MemoryStats> GetStatsAsync()
    {
        EnsureInitialized();
        lock (_lock)
        {
            using var conn = OpenConnection();
            return Task.FromResult(GetStatsInternal(conn));
        }
    }

    /// <summary>
    /// 构建系统提示注入文本：将相关记忆注入到 LLM 系统提示中。
    /// 灵感：Hermes Agent 的 MEMORY.md 注入模式。
    /// 仅返回条目列表；由后端统一框定为「用户记忆（仅作偏好参考）」，
    /// 避免与检索 query 混写导致主题污染。
    /// </summary>
    public async Task<string> BuildSystemPromptInjectionAsync(string userMessage, int maxEntries = 8)
    {
        if (string.IsNullOrWhiteSpace(userMessage)) return string.Empty;

        var results = await SearchAsync(userMessage, limit: maxEntries);
        if (results.Count == 0) return string.Empty;

        var sb = new StringBuilder();
        foreach (var r in results)
        {
            sb.AppendLine($"- {r.Entry.Content}");
        }
        return sb.ToString();
    }

    // ═══════════════════════════════════════════════════════
    //  内部方法
    // ═══════════════════════════════════════════════════════

    private static bool IsDuplicate(SqliteConnection conn, string content)
    {
        var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT COUNT(*) FROM user_memory WHERE content=$c";
        cmd.Parameters.AddWithValue("$c", content);
        return (long)cmd.ExecuteScalar()! > 0;
    }

    private MemoryStats GetStatsInternal(SqliteConnection conn)
    {
        var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT COALESCE(SUM(LENGTH(content)), 0), COUNT(*) FROM user_memory";
        using var reader = cmd.ExecuteReader();
        if (reader.Read())
        {
            return new MemoryStats
            {
                UsedChars = reader.GetInt32(0),
                MaxChars = DefaultMaxChars,
                EntryCount = reader.GetInt32(1)
            };
        }
        return new MemoryStats { MaxChars = DefaultMaxChars };
    }

    private static MemoryEntry ReadEntry(SqliteDataReader reader) => new()
    {
        Id = reader.GetInt64(0),
        Category = reader.GetString(1),
        Content = reader.GetString(2),
        Source = reader.GetString(3),
        CreatedAt = DateTime.TryParse(reader.GetString(4), out var cat) ? cat : DateTime.MinValue,
        UpdatedAt = DateTime.TryParse(reader.GetString(5), out var uat) ? uat : DateTime.MinValue,
    };

    /// <summary>构建 FTS5 查询字符串：对中文按词/双字切分并用 OR 连接。</summary>
    private static string BuildFtsQuery(string query)
    {
        query = query.Trim();
        // 如果查询已经是 FTS5 语法（包含引号、*、OR、AND 等），直接使用
        if (query.Contains('"') || query.Contains('*') || query.Contains(" OR ") || query.Contains(" AND "))
            return query;

        // 中文分词：按标点/空格拆分，CJK 连续字符生成 2-gram
        var tokens = new List<string>();
        var segments = System.Text.RegularExpressions.Regex.Split(query, @"[\s,.;!?。，；！？、：:""'""\[\]\(\)（）《》<>]+");
        foreach (var seg in segments)
        {
            if (string.IsNullOrWhiteSpace(seg)) continue;
            // 将段落拆分为 FTS5 token：CJK 逐字，Latin 按词
            var subTokens = SegmentQuerySegment(seg);
            tokens.AddRange(subTokens);
        }
        if (tokens.Count == 0)
            return query; // fallback: 原样返回让 FTS5 尝试

        // 收紧：CJK 单字几乎无信息，OR 召回会命中整库（真实故障：问 GPT 却注入豆包记忆）。
        // 仅保留 Latin 词（≥2 字符）与 CJK 双字组合；若过滤后为空则视为无可靠召回。
        var meaningful = tokens
            .Where(t => t.Length >= 2 || !IsCjkToken(t))
            .Where(t => !(t.Length == 1 && IsCjkToken(t)))
            .ToList();
        // CJK 双字滑窗：把连续单字合成 bigram
        for (var i = 0; i < tokens.Count - 1; i++)
        {
            if (IsCjkToken(tokens[i]) && IsCjkToken(tokens[i + 1]))
                meaningful.Add(tokens[i] + tokens[i + 1]);
        }
        meaningful = meaningful.Distinct().ToList();
        if (meaningful.Count == 0)
            return "\"__no_match__\""; // 故意无命中，避免单字 OR 污染
        if (meaningful.Count == 1)
            return $"\"{meaningful[0]}\"*";
        // 多个 token：用 OR 连接（宽松匹配，召回优先）
        return string.Join(" OR ", meaningful.Select(t => $"\"{t}\""));
    }

    private static bool IsCjkToken(string t) =>
        t.Length == 1 && IsCjk(t[0]);

    /// <summary>将查询段落拆分为 FTS5 token：CJK 逐字，Latin 按词。</summary>
    private static List<string> SegmentQuerySegment(string seg)
    {
        var tokens = new List<string>();
        var latinBuf = new System.Text.StringBuilder();
        foreach (var ch in seg)
        {
            if (IsCjk(ch))
            {
                if (latinBuf.Length > 0) { tokens.Add(latinBuf.ToString()); latinBuf.Clear(); }
                tokens.Add(ch.ToString());
            }
            else
            {
                latinBuf.Append(ch);
            }
        }
        if (latinBuf.Length > 0) tokens.Add(latinBuf.ToString());
        return tokens;
    }

    private static bool IsCjk(char c) =>
        (c >= 0x4E00 && c <= 0x9FFF) ||   // CJK 基本
        (c >= 0x3400 && c <= 0x4DBF) ||   // CJK 扩展 A
        (c >= 0xF900 && c <= 0xFAFF);     // CJK 兼容

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

    public void Dispose()
    {
        // SqliteConnection 是按需创建的，无需长期持有
    }
}
