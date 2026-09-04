using System.IO;
using System.Text.Json;
using DocMind.Models;
using Microsoft.Data.Sqlite;

namespace DocMind.Services;

/// <summary>
/// LLM 调用成本追踪：记录每次调用的 token 数和费用。
/// 灵感来源：Hive 框架的三级成本强制执行。
///
/// 设计要点：
/// - SQLite 存储（%LOCALAPPDATA%/DocMind/cost_tracker.db）
/// - 每次 LLM 调用后记录：模型、提供商、prompt/completion tokens、费用
/// - 支持查询今日/本月费用、按模型分组
/// - 预算阈值告警（80% 黄色，95% 红色）
/// </summary>
public sealed class CostTracker : IDisposable
{
    private readonly string _dbPath;
    private readonly object _lock = new();
    private bool _initialized;

    // 默认模型定价（USD per 1K tokens）—— 可扩展为配置
    private static readonly Dictionary<string, (double prompt, double completion)> DefaultPricing = new(StringComparer.OrdinalIgnoreCase)
    {
        ["gpt-4o"] = (0.0025, 0.01),
        ["gpt-4o-mini"] = (0.00015, 0.0006),
        ["gpt-4-turbo"] = (0.01, 0.03),
        ["gpt-4"] = (0.03, 0.06),
        ["gpt-3.5-turbo"] = (0.0005, 0.0015),
        ["claude-3-5-sonnet"] = (0.003, 0.015),
        ["claude-3-5-haiku"] = (0.00025, 0.00125),
        ["deepseek-chat"] = (0.00014, 0.00028),
        ["deepseek-reasoner"] = (0.00055, 0.00219),
    };

    public CostTracker()
    {
        var configDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "DocMind");
        Directory.CreateDirectory(configDir);
        _dbPath = Path.Combine(configDir, "cost_tracker.db");
    }

    /// <summary>用于单元测试的临时路径构造。</summary>
    internal CostTracker(string dbPath)
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
            Execute(conn, """
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    chat_id TEXT,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_llm_calls_timestamp ON llm_calls(timestamp);
                CREATE INDEX IF NOT EXISTS idx_llm_calls_model ON llm_calls(model);
            """);
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

    // ═══════════════════════════════════════════════════════
    //  公共 API
    // ═══════════════════════════════════════════════════════

    /// <summary>记录一次 LLM 调用。</summary>
    public Task LogCallAsync(string model, string provider, int promptTokens, int completionTokens,
        decimal? costUsd = null, string? chatId = null)
    {
        EnsureInitialized();

        var cost = costUsd ?? EstimateCost(model, promptTokens, completionTokens);

        lock (_lock)
        {
            using var conn = OpenConnection();
            Execute(conn,
                "INSERT INTO llm_calls (model, provider, prompt_tokens, completion_tokens, cost_usd, chat_id) " +
                "VALUES ($model, $provider, $pt, $ct, $cost, $chat)",
                ("$model", model), ("$provider", provider),
                ("$pt", promptTokens), ("$ct", completionTokens),
                ("$cost", (double)cost), ("$chat", chatId ?? (object)DBNull.Value));
        }
        return Task.CompletedTask;
    }

    /// <summary>获取今日费用（USD）。</summary>
    public Task<decimal> GetTodayCostAsync()
    {
        EnsureInitialized();
        lock (_lock)
        {
            using var conn = OpenConnection();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE date(timestamp) = date('now')";
            return Task.FromResult(Convert.ToDecimal(cmd.ExecuteScalar()));
        }
    }

    /// <summary>获取本月费用（USD）。</summary>
    public Task<decimal> GetMonthCostAsync()
    {
        EnsureInitialized();
        lock (_lock)
        {
            using var conn = OpenConnection();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')";
            return Task.FromResult(Convert.ToDecimal(cmd.ExecuteScalar()));
        }
    }

    /// <summary>获取按模型分组的费用（本月）。</summary>
    public Task<IReadOnlyList<CostByModel>> GetCostByModelAsync()
    {
        EnsureInitialized();
        lock (_lock)
        {
            using var conn = OpenConnection();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT model,
                       SUM(cost_usd) as total_cost,
                       COUNT(*) as call_count,
                       SUM(prompt_tokens) as total_prompt,
                       SUM(completion_tokens) as total_completion
                FROM llm_calls
                WHERE strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')
                GROUP BY model
                ORDER BY total_cost DESC
            """;

            var results = new List<CostByModel>();
            using var reader = cmd.ExecuteReader();
            while (reader.Read())
            {
                results.Add(new CostByModel
                {
                    Model = reader.GetString(0),
                    TotalCostUsd = Convert.ToDecimal(reader.GetDouble(1)),
                    CallCount = reader.GetInt32(2),
                    TotalPromptTokens = reader.GetInt32(3),
                    TotalCompletionTokens = reader.GetInt32(4),
                });
            }
            return Task.FromResult<IReadOnlyList<CostByModel>>(results);
        }
    }

    /// <summary>获取每日费用趋势（最近 30 天）。</summary>
    public Task<IReadOnlyList<DailyCost>> GetDailyCostsAsync(int days = 30)
    {
        EnsureInitialized();
        lock (_lock)
        {
            using var conn = OpenConnection();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                SELECT date(timestamp) as day,
                       SUM(cost_usd) as total_cost,
                       COUNT(*) as call_count
                FROM llm_calls
                WHERE timestamp >= datetime('now', '-' || $days || ' days')
                GROUP BY day
                ORDER BY day
            """;
            cmd.Parameters.AddWithValue("$days", days);

            var results = new List<DailyCost>();
            using var reader = cmd.ExecuteReader();
            while (reader.Read())
            {
                results.Add(new DailyCost
                {
                    Date = reader.GetString(0),
                    TotalCostUsd = Convert.ToDecimal(reader.GetDouble(1)),
                    CallCount = reader.GetInt32(2),
                });
            }
            return Task.FromResult<IReadOnlyList<DailyCost>>(results);
        }
    }

    /// <summary>检查预算状态。</summary>
    public Task<BudgetStatus> CheckBudgetAsync(decimal? monthlyBudget = null)
    {
        EnsureInitialized();
        var today = GetTodayCostAsync().Result;
        var month = GetMonthCostAsync().Result;

        return Task.FromResult(new BudgetStatus
        {
            TodayCost = today,
            MonthCost = month,
            MonthlyBudget = monthlyBudget,
            IsNearLimit = monthlyBudget.HasValue && month > monthlyBudget.Value * 0.8m,
            IsAtLimit = monthlyBudget.HasValue && month > monthlyBudget.Value * 0.95m,
        });
    }

    /// <summary>
    /// 估算调用费用（基于 token 数和已知模型定价）。
    /// 未知模型返回 0（不阻断记录）。
    /// </summary>
    public static decimal EstimateCost(string model, int promptTokens, int completionTokens)
    {
        if (string.IsNullOrWhiteSpace(model)) return 0;

        // 尝试精确匹配
        if (DefaultPricing.TryGetValue(model, out var pricing))
        {
            return (decimal)(promptTokens / 1000.0 * pricing.prompt + completionTokens / 1000.0 * pricing.completion);
        }

        // 尝试模糊匹配（如 "gpt-4o-2024-08-06" 匹配 "gpt-4o"）
        foreach (var (key, value) in DefaultPricing)
        {
            if (model.StartsWith(key, StringComparison.OrdinalIgnoreCase))
            {
                return (decimal)(promptTokens / 1000.0 * value.prompt + completionTokens / 1000.0 * value.completion);
            }
        }

        return 0;
    }

    // ═══════════════════════════════════════════════════════

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

    public void Dispose()
    {
        // SqliteConnection 是按需创建的，无需长期持有
    }
}

// ═══════════════════════════════════════════════════════
//  数据模型
// ═══════════════════════════════════════════════════════

public sealed class CostByModel
{
    public string Model { get; set; } = string.Empty;
    public decimal TotalCostUsd { get; set; }
    public int CallCount { get; set; }
    public int TotalPromptTokens { get; set; }
    public int TotalCompletionTokens { get; set; }
}

public sealed class DailyCost
{
    public string Date { get; set; } = string.Empty;
    public decimal TotalCostUsd { get; set; }
    public int CallCount { get; set; }
}

public sealed class BudgetStatus
{
    public decimal TodayCost { get; set; }
    public decimal MonthCost { get; set; }
    public decimal? MonthlyBudget { get; set; }
    public bool IsNearLimit { get; set; }
    public bool IsAtLimit { get; set; }
}
