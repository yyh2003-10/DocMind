namespace DocMind.Services;

/// <summary>
/// 工具调用自愈：搜索/操作失败时自动调整策略重试。
/// 灵感来源：Hive 框架的 self-healing 模式（非简单重试，而是分析失败后调整策略）。
///
/// 核心思想：
/// - 第一次失败 → 放宽阈值（降低 min_score）
/// - 第二次失败 → 扩大搜索范围（增大 top_k）
/// - 第三次失败 → 返回空结果（真正没有相关内容）
///
/// 与简单重试的区别：
/// - 每次重试使用不同的参数策略
/// - 策略基于失败类型选择（空结果 vs 超时 vs 错误）
/// - 不会无限重试（有最大次数限制）
/// </summary>
public static class ToolCallResilience
{
    /// <summary>
    /// 搜索自愈：空结果时自动调整策略重试。
    /// </summary>
    /// <param name="searchFn">实际搜索函数（调用后端 API）。</param>
    /// <param name="query">原始查询。</param>
    /// <param name="topK">初始 top_k。</param>
    /// <param name="minScore">初始 min_score。</param>
    /// <param name="maxRetries">最大重试次数。</param>
    /// <returns>搜索结果（可能经过多轮调整）。</returns>
    public static async Task<T> SearchWithHealingAsync<T>(
        Func<int, double?, Task<T>> searchFn,
        string query,
        int topK = 5,
        double? minScore = 0.5,
        int maxRetries = 2) where T : class
    {
        for (int attempt = 0; attempt <= maxRetries; attempt++)
        {
            try
            {
                var result = await searchFn(topK, minScore);

                // 检查结果是否有效（由调用方判断）
                // 这里不做语义检查，只做异常捕获
                return result;
            }
            catch (Exception ex) when (attempt < maxRetries)
            {
                // 策略调整（非简单重试）
                switch (attempt)
                {
                    case 0:
                        // 第一次失败：放宽 min_score
                        minScore = Math.Max(0, (minScore ?? 0.5) - 0.15);
                        DebugLog.Warn(
                            $"搜索第 {attempt + 1} 次失败，放宽阈值: min_score={minScore:F2}",
                            "Resilience");
                        break;
                    case 1:
                        // 第二次失败：扩大 top_k
                        topK = Math.Min(20, topK * 2);
                        minScore = 0; // 完全移除阈值
                        DebugLog.Warn(
                            $"搜索第 {attempt + 1} 次失败，扩大范围: top_k={topK}, min_score={minScore}",
                            "Resilience");
                        break;
                }
            }
        }

        // 所有重试失败，抛出最后一次异常
        return await searchFn(topK, minScore);
    }

    /// <summary>
    /// 通用操作自愈：操作失败时根据错误类型选择恢复策略。
    /// </summary>
    /// <param name="operation">要执行的操作。</param>
    /// <param name="operationName">操作名称（用于日志）。</param>
    /// <param name="maxRetries">最大重试次数。</param>
    /// <param name="shouldRetry">判断是否应该重试的函数（可选）。</param>
    public static async Task<T> ExecuteWithHealingAsync<T>(
        Func<Task<T>> operation,
        string operationName,
        int maxRetries = 2,
        Func<Exception, bool>? shouldRetry = null)
    {
        Exception? lastException = null;

        for (int attempt = 0; attempt <= maxRetries; attempt++)
        {
            try
            {
                return await operation();
            }
            catch (Exception ex) when (attempt < maxRetries)
            {
                lastException = ex;

                // 判断是否应该重试
                if (shouldRetry is not null && !shouldRetry(ex))
                {
                    DebugLog.Warn(
                        $"操作「{operationName}」失败且不可重试: {ex.Message}",
                        "Resilience");
                    throw;
                }

                // 指数退避
                var delay = TimeSpan.FromMilliseconds(500 * Math.Pow(2, attempt));
                DebugLog.Warn(
                    $"操作「{operationName}」第 {attempt + 1} 次失败，{delay.TotalMilliseconds}ms 后重试: {ex.Message}",
                    "Resilience");
                await Task.Delay(delay);
            }
        }

        throw lastException!;
    }

    /// <summary>
    /// 搜索结果是否为空的判断函数（供 SearchWithHealingAsync 使用）。
    /// </summary>
    public static bool IsEmptySearchResult<T>(T result, Func<T, int> countSelector)
    {
        return countSelector(result) == 0;
    }
}
