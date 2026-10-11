using System.IO;
using DocMind.Models;
using Microsoft.Data.Sqlite;

namespace DocMind.Services;

/// <summary>
/// 会话全文搜索服务：直连后端 SQLite（只读），搜索所有历史对话。
///
/// 设计要点：
/// - 与后端共用同一个 doc2mind.db 数据库（只读连接）
/// - 后端 chat_messages 实际列名为 created_at（不是 timestamp）
/// - 优先 FTS5（chat_messages_fts，CJK 已分词入库）；表不存在时回退 LIKE
/// - 零 LLM 成本
/// </summary>
public sealed class SessionSearchService
{
    private readonly string _dbPath;

    public SessionSearchService()
    {
        var dataDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "doc2mind");
        _dbPath = Path.Combine(dataDir, "doc2mind.db");
    }

    /// <summary>用于单元测试的临时路径构造。</summary>
    internal SessionSearchService(string dbPath)
    {
        _dbPath = dbPath;
    }

    /// <summary>数据库文件是否存在。</summary>
    public bool DatabaseExists => File.Exists(_dbPath);

    /// <summary>
    /// 搜索历史对话（FTS5 优先，失败/无表时 LIKE 回退）。
    /// </summary>
    public Task<IReadOnlyList<SessionSearchResult>> SearchAsync(
        string query, int limit = 10, string? sessionId = null)
    {
        if (!DatabaseExists || string.IsNullOrWhiteSpace(query))
            return Task.FromResult<IReadOnlyList<SessionSearchResult>>(
                Array.Empty<SessionSearchResult>());

        try
        {
            using var conn = new SqliteConnection($"Data Source={_dbPath};Mode=ReadOnly");
            conn.Open();

            var results = new List<SessionSearchResult>();

            if (TableExists(conn, "chat_messages_fts"))
            {
                try
                {
                    SearchViaFts(conn, query, limit, sessionId, results);
                    return Task.FromResult<IReadOnlyList<SessionSearchResult>>(results);
                }
                catch (Exception ex)
                {
                    DebugLog.Warn($"FTS 会话搜索失败，回退 LIKE: {ex.Message}", "SessionSearch");
                    results.Clear();
                }
            }

            SearchViaLike(conn, query, limit, sessionId, results);
            return Task.FromResult<IReadOnlyList<SessionSearchResult>>(results);
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"会话搜索失败: {ex.Message}", "SessionSearch");
            return Task.FromResult<IReadOnlyList<SessionSearchResult>>(
                Array.Empty<SessionSearchResult>());
        }
    }

    /// <summary>获取指定会话的完整历史消息。</summary>
    public Task<IReadOnlyList<SessionSearchResult>> GetSessionHistoryAsync(string chatId, int limit = 50)
    {
        if (!DatabaseExists || string.IsNullOrWhiteSpace(chatId))
            return Task.FromResult<IReadOnlyList<SessionSearchResult>>(
                Array.Empty<SessionSearchResult>());

        try
        {
            using var conn = new SqliteConnection($"Data Source={_dbPath};Mode=ReadOnly");
            conn.Open();

            using var cmd = conn.CreateCommand();
            // 后端实际列名是 created_at（历史 bug：曾误用 timestamp 导致读取必失败）
            cmd.CommandText = """
                SELECT content, role, created_at, chat_id
                FROM chat_messages
                WHERE chat_id = $sid
                ORDER BY id DESC
                LIMIT $limit
            """;
            cmd.Parameters.AddWithValue("$sid", chatId);
            cmd.Parameters.AddWithValue("$limit", limit);

            var results = new List<SessionSearchResult>();
            using var reader = cmd.ExecuteReader();
            while (reader.Read())
            {
                results.Add(ReadResult(reader));
            }
            results.Reverse(); // 按时间正序
            return Task.FromResult<IReadOnlyList<SessionSearchResult>>(results);
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"获取会话历史失败: {ex.Message}", "SessionSearch");
            return Task.FromResult<IReadOnlyList<SessionSearchResult>>(
                Array.Empty<SessionSearchResult>());
        }
    }

    // ═══════════════════════════════════════════════════════

    private static void SearchViaFts(
        SqliteConnection conn, string query, int limit, string? sessionId,
        List<SessionSearchResult> results)
    {
        var ftsQuery = BuildFtsQuery(query);
        var safeQuery = ftsQuery.Replace("'", "''");

        var sql = sessionId is null
            ? $"""
                SELECT m.content, m.role, m.created_at, m.chat_id,
                       COALESCE(s.title, ''), fts.rank
                FROM chat_messages_fts fts
                JOIN chat_messages m ON m.rowid = fts.rowid
                LEFT JOIN chat_sessions s ON s.id = m.chat_id
                WHERE chat_messages_fts MATCH '{safeQuery}'
                ORDER BY fts.rank
                LIMIT $limit
              """
            : $"""
                SELECT m.content, m.role, m.created_at, m.chat_id,
                       COALESCE(s.title, ''), fts.rank
                FROM chat_messages_fts fts
                JOIN chat_messages m ON m.rowid = fts.rowid
                LEFT JOIN chat_sessions s ON s.id = m.chat_id
                WHERE chat_messages_fts MATCH '{safeQuery}' AND m.chat_id = $sid
                ORDER BY fts.rank
                LIMIT $limit
              """;

        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        cmd.Parameters.AddWithValue("$limit", limit);
        if (sessionId is not null) cmd.Parameters.AddWithValue("$sid", sessionId);

        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            var r = ReadResult(reader);
            results.Add(new SessionSearchResult
            {
                Content = r.Content,
                Role = r.Role,
                Timestamp = r.Timestamp,
                SessionId = r.SessionId,
                SessionTitle = reader.GetString(4),
                Rank = reader.IsDBNull(5) ? 0 : reader.GetDouble(5),
            });
        }
    }

    private static void SearchViaLike(
        SqliteConnection conn, string query, int limit, string? sessionId,
        List<SessionSearchResult> results)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sessionId is null
            ? """
                SELECT m.content, m.role, m.created_at, m.chat_id,
                       COALESCE(s.title, ''), 0.0 AS rank
                FROM chat_messages m
                LEFT JOIN chat_sessions s ON s.id = m.chat_id
                WHERE m.content LIKE $pat
                ORDER BY m.id DESC
                LIMIT $limit
              """
            : """
                SELECT m.content, m.role, m.created_at, m.chat_id,
                       COALESCE(s.title, ''), 0.0 AS rank
                FROM chat_messages m
                LEFT JOIN chat_sessions s ON s.id = m.chat_id
                WHERE m.content LIKE $pat AND m.chat_id = $sid
                ORDER BY m.id DESC
                LIMIT $limit
              """;
        cmd.Parameters.AddWithValue("$pat", $"%{query.Trim()}%");
        cmd.Parameters.AddWithValue("$limit", limit);
        if (sessionId is not null) cmd.Parameters.AddWithValue("$sid", sessionId);

        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            var r = ReadResult(reader);
            results.Add(new SessionSearchResult
            {
                Content = r.Content,
                Role = r.Role,
                Timestamp = r.Timestamp,
                SessionId = r.SessionId,
                SessionTitle = reader.GetString(4),
                Rank = 0.0,
            });
        }
    }

    private static SessionSearchResult ReadResult(SqliteDataReader reader) => new()
    {
        Content = reader.GetString(0),
        Role = reader.GetString(1),
        Timestamp = DateTime.TryParse(reader.GetString(2), out var ts) ? ts : DateTime.MinValue,
        SessionId = reader.GetString(3),
    };

    private static bool TableExists(SqliteConnection conn, string tableName)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=$name";
        cmd.Parameters.AddWithValue("$name", tableName);
        return (long)cmd.ExecuteScalar()! > 0;
    }

    /// <summary>
    /// 构建 FTS5 查询：后端 FTS 对 CJK 按字入库（unicode61 + 分词空格），
    /// 中文需拆成单字 AND / 双字短语，否则 MATCH 整词永远空结果。
    /// </summary>
    internal static string BuildFtsQuery(string query)
    {
        query = query.Trim();
        if (query.Contains('"') || query.Contains('*') || query.Contains(" OR ") || query.Contains(" AND "))
            return query;

        var tokens = new List<string>();
        var latin = new System.Text.StringBuilder();
        foreach (var ch in query)
        {
            if (IsCjk(ch))
            {
                if (latin.Length > 0) { tokens.Add(latin.ToString()); latin.Clear(); }
                tokens.Add(ch.ToString());
            }
            else if (char.IsWhiteSpace(ch) || ch is ',' or '.' or ';' or '!' or '?')
            {
                if (latin.Length > 0) { tokens.Add(latin.ToString()); latin.Clear(); }
            }
            else
            {
                latin.Append(ch);
            }
        }
        if (latin.Length > 0) tokens.Add(latin.ToString());

        var meaningful = new List<string>();
        for (int i = 0; i < tokens.Count; i++)
        {
            var t = tokens[i];
            if (t.Length == 1 && IsCjk(t[0]))
            {
                // CJK 单字：与相邻 CJK 合成双字短语；孤立单字仍保留
                if (i + 1 < tokens.Count && tokens[i + 1].Length == 1 && IsCjk(tokens[i + 1][0]))
                    meaningful.Add($"\"{t}{tokens[i + 1]}\"");
                else
                    meaningful.Add($"\"{t}\"");
            }
            else if (t.Length >= 2)
            {
                meaningful.Add($"\"{t}\"");
            }
        }
        meaningful = meaningful.Distinct().ToList();
        if (meaningful.Count == 0)
            return "\"__no_match__\"";
        if (meaningful.Count == 1)
            return meaningful[0];
        return string.Join(" OR ", meaningful);
    }

    private static bool IsCjk(char c) =>
        (c >= 0x4E00 && c <= 0x9FFF) ||
        (c >= 0x3400 && c <= 0x4DBF) ||
        (c >= 0xF900 && c <= 0xFAFF);
}
