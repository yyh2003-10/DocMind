using System.IO;
using DocMind.Models;
using Microsoft.Data.Sqlite;

namespace DocMind.Services;

/// <summary>
/// 会话全文搜索服务：基于 SQLite FTS5，搜索所有历史对话。
/// 灵感来源：Hermes Agent 的 session_search 工具。
///
/// 设计要点：
/// - 与后端共用同一个 doc2mind.db 数据库（只读连接）
/// - FTS5 全文检索，~20ms 延迟
/// - 支持按会话 ID 过滤
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
    /// 搜索历史对话（FTS5 全文检索）。
    /// </summary>
    /// <param name="query">搜索关键词。</param>
    /// <param name="limit">返回结果上限。</param>
    /// <param name="sessionId">可选：仅搜索指定会话。</param>
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

            // 检查 FTS5 表是否存在
            if (!TableExists(conn, "chat_messages_fts"))
                return Task.FromResult<IReadOnlyList<SessionSearchResult>>(
                    Array.Empty<SessionSearchResult>());

            var ftsQuery = BuildFtsQuery(query);

            var sql = sessionId is null
                ? """
                    SELECT m.content, m.role, m.timestamp, m.chat_id,
                           COALESCE(s.title, ''), fts.rank
                    FROM chat_messages_fts fts
                    JOIN chat_messages m ON m.rowid = fts.rowid
                    LEFT JOIN chat_sessions s ON s.id = m.chat_id
                    WHERE chat_messages_fts MATCH $query
                    ORDER BY fts.rank
                    LIMIT $limit
                  """
                : """
                    SELECT m.content, m.role, m.timestamp, m.chat_id,
                           COALESCE(s.title, ''), fts.rank
                    FROM chat_messages_fts fts
                    JOIN chat_messages m ON m.rowid = fts.rowid
                    LEFT JOIN chat_sessions s ON s.id = m.chat_id
                    WHERE chat_messages_fts MATCH $query AND m.chat_id = $sid
                    ORDER BY fts.rank
                    LIMIT $limit
                  """;

            using var cmd = conn.CreateCommand();
            cmd.CommandText = sql;
            cmd.Parameters.AddWithValue("$query", ftsQuery);
            cmd.Parameters.AddWithValue("$limit", limit);
            if (sessionId is not null) cmd.Parameters.AddWithValue("$sid", sessionId);

            var results = new List<SessionSearchResult>();
            using var reader = cmd.ExecuteReader();
            while (reader.Read())
            {
                results.Add(new SessionSearchResult
                {
                    Content = reader.GetString(0),
                    Role = reader.GetString(1),
                    Timestamp = DateTime.TryParse(reader.GetString(2), out var ts) ? ts : DateTime.MinValue,
                    SessionId = reader.GetString(3),
                    SessionTitle = reader.GetString(4),
                    Rank = reader.GetDouble(5)
                });
            }
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
            cmd.CommandText = """
                SELECT content, role, timestamp, chat_id
                FROM chat_messages
                WHERE chat_id = $sid
                ORDER BY rowid DESC
                LIMIT $limit
            """;
            cmd.Parameters.AddWithValue("$sid", chatId);
            cmd.Parameters.AddWithValue("$limit", limit);

            var results = new List<SessionSearchResult>();
            using var reader = cmd.ExecuteReader();
            while (reader.Read())
            {
                results.Add(new SessionSearchResult
                {
                    Content = reader.GetString(0),
                    Role = reader.GetString(1),
                    Timestamp = DateTime.TryParse(reader.GetString(2), out var ts) ? ts : DateTime.MinValue,
                    SessionId = reader.GetString(3),
                });
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

    private static bool TableExists(SqliteConnection conn, string tableName)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=$name";
        cmd.Parameters.AddWithValue("$name", tableName);
        return (long)cmd.ExecuteScalar()! > 0;
    }

    private static string BuildFtsQuery(string query)
    {
        query = query.Trim();
        if (query.Contains('"') || query.Contains('*') || query.Contains(" OR ") || query.Contains(" AND "))
            return query;

        var tokens = query.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (tokens.Length == 1)
            return $"\"{tokens[0]}\"*";

        return string.Join(" AND ", tokens.Select(t => $"\"{t}\""));
    }
}
