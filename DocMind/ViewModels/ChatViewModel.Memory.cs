using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;
using Markdig;
using Markdig.Wpf;

namespace DocMind.ViewModels;

public partial class ChatViewModel : ViewModelBase
{
    // ═══════════════════════════════════════════════════════
    //  Phase 1: 跨会话记忆 — 自动提取
    // ═══════════════════════════════════════════════════════

    /// <summary>
    /// 对话结束后异步提取关键事实存入用户记忆。
    /// 灵感：Hermes Agent 的后台自我审查循环 + Mem0 的事实提取管线。
    /// 采用纯规则提取（零 LLM 成本），覆盖最常见的记忆场景。
    /// </summary>
    private async Task ExtractAndStoreMemoryAsync(string userMessage, string assistantResponse)
    {
        if (_userMemory is null || !_appSettings.MemoryEnabled || !_appSettings.MemoryAutoExtract)
            return;

        try
        {
            var entries = new List<(string content, string category, string source)>();
            var userLower = userMessage.ToLowerInvariant();

            // ── 规则 1: 用户纠正（"不要"、"别用"、"以后用"、"改为"） ──
            if (MatchesAny(userLower, "不要", "别用", "别再", "以后用", "改为", "换成", "请用", "记住"))
            {
                // 提取纠正内容：取用户消息的前 200 字符作为记忆
                var memContent = userMessage.Length > 200 ? userMessage[..200] + "…" : userMessage;
                entries.Add((memContent, "memory", "manual"));
            }

            // ── 规则 2: 用户偏好表达（"我更喜欢"、"我希望"、"请总是"） ──
            if (MatchesAny(userLower, "我更喜欢", "我喜欢", "我希望", "请总是", "每次都", "默认用"))
            {
                var memContent = userMessage.Length > 200 ? userMessage[..200] + "…" : userMessage;
                entries.Add((memContent, "user", "manual"));
            }

            // ── 规则 3: 环境/项目信息（"我的项目在"、"这台机器"、"使用的是"） ──
            if (MatchesAny(userLower, "我的项目", "这台机器", "使用的是", "操作系统", "电脑是"))
            {
                var memContent = userMessage.Length > 200 ? userMessage[..200] + "…" : userMessage;
                entries.Add((memContent, "memory", "auto"));
            }

            // ── 规则 4: 人名/组织信息（"我是"、"我在"、"我们公司"） ──
            if (MatchesAny(userLower, "我是", "我在", "我们公司", "我们团队", "我叫"))
            {
                var memContent = userMessage.Length > 150 ? userMessage[..150] + "…" : userMessage;
                entries.Add((memContent, "user", "auto"));
            }

            // ── 规则 5: 助手回答中的关键结论（以"总结"、"结论"、"建议"开头的段落） ──
            if (!string.IsNullOrWhiteSpace(assistantResponse))
            {
                var lines = assistantResponse.Split('\n', StringSplitOptions.RemoveEmptyEntries);
                foreach (var line in lines)
                {
                    var trimmed = line.TrimStart('#', ' ', '-', '*');
                    if (trimmed.Length > 20 && trimmed.Length < 200 &&
                        MatchesAny(trimmed.ToLowerInvariant(), "总结", "结论", "建议", "关键", "核心"))
                    {
                        entries.Add((trimmed, "memory", "auto"));
                    }
                }
            }

            // 去重并存储（每轮最多存 3 条，避免记忆膨胀）
            int stored = 0;
            foreach (var (content, category, source) in entries.DistinctBy(e => e.content))
            {
                if (stored >= 3) break;
                if (await _userMemory.AddAsync(content, category, source))
                {
                    stored++;
                }
            }

            if (stored > 0)
            {
                DebugLog.Info($"记忆自动提取: 新增 {stored} 条（来源: {string.Join(",", entries.Take(stored).Select(e => e.source))}）", "Memory");
            }
        }
        catch (Exception ex)
        {
            // 记忆提取失败不阻断对话
            DebugLog.Warn($"记忆提取异常（不阻断对话）: {ex.Message}", "Memory");
        }
    }

    /// <summary>手动将一条消息保存为记忆（供 UI "记住这个" 菜单调用）。</summary>
    public async Task RememberMessageAsync(ChatMessage message)
    {
        if (_userMemory is null || message.Content.Length < 5) return;
        var content = message.Content.Length > 300 ? message.Content[..300] + "…" : message.Content;
        var category = message.IsUser ? "user" : "memory";
        if (await _userMemory.AddAsync(content, category, "manual"))
        {
            _notifications?.Success("已记住这条内容", "记忆");
            StatusMessage = "已保存到用户记忆";
        }
        else
        {
            _notifications?.Info("该内容已在记忆中或容量已满", "记忆");
        }
    }

    /// <summary>记住这条消息（UI 命令入口）。</summary>
    [RelayCommand]
    private Task RememberMessage(ChatMessage? message)
        => message is null ? Task.CompletedTask : RememberMessageAsync(message);

    /// <summary>为消息挂上反馈落库回调（点赞/点踩写 FeedbackService，不再只是本地布尔）。</summary>
    private void AttachFeedbackSink(ChatMessage msg)
    {
        msg.FeedbackSink = async (m, isLike) =>
        {
            if (isLike) await ThumbsUpAsync(m);
            else await ThumbsDownAsync(m);
        };
    }

    /// <summary>获取记忆统计信息（供 UI 展示）。</summary>
    public async Task<Models.MemoryStats?> GetMemoryStatsAsync()
    {
        if (_userMemory is null) return null;
        try { return await _userMemory.GetStatsAsync(); }
        catch { return null; }
    }

    // ── 反馈循环（Phase 4） ──

    /// <summary>用户对助手回答点赞。</summary>
    public async Task ThumbsUpAsync(ChatMessage message)
    {
        if (_feedback is null || message.Role != "assistant") return;
        var msgId = GetStableMessageId(message);
        var query = Messages.LastOrDefault(m => m.Role == "user")?.Content;
        await _feedback.SubmitFeedbackAsync(msgId, "up",
            chatId: _chatId, querySnapshot: query, responseSnapshot: message.Content);
        _notifications?.Success("感谢您的反馈！", "反馈");
        StatusMessage = "已记录好评 👍";
    }

    /// <summary>用户对助手回答点踩，并可附带纠正文本。</summary>
    public async Task ThumbsDownAsync(ChatMessage message, string? correction = null)
    {
        if (_feedback is null || message.Role != "assistant") return;
        var msgId = GetStableMessageId(message);
        var query = Messages.LastOrDefault(m => m.Role == "user")?.Content;
        await _feedback.SubmitFeedbackAsync(msgId, "down",
            correction: correction, chatId: _chatId,
            querySnapshot: query, responseSnapshot: message.Content);
        _notifications?.Info("已记录反馈，我们会持续改进", "反馈");
        StatusMessage = "已记录差评 👎";
        // 达到阈值时触发自动分析，提取经验教训存入记忆
        try
        {
            if (_userMemory is not null && await _feedback.ShouldAnalyzeAsync())
            {
                var added = await _feedback.AnalyzeAndStoreInsightsAsync(_userMemory);
                if (added > 0)
                {
                    DebugLog.Info($"反馈自动分析：新增 {added} 条经验到记忆", "Feedback");
                }
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"反馈分析异常（不阻断对话）: {ex.Message}", "Feedback");
        }
    }

    /// <summary>获取反馈统计（供 UI 展示）。</summary>
    public async Task<FeedbackStats?> GetFeedbackStatsAsync()
    {
        if (_feedback is null) return null;
        try { return await _feedback.GetStatsAsync(); }
        catch { return null; }
    }

    /// <summary>生成消息的稳定 ID（用于反馈关联）。</summary>
    private static string GetStableMessageId(ChatMessage msg)
    {
        // 用内容哈希 + 时间戳作为稳定 ID
        var contentHash = msg.Content.GetHashCode().ToString("X8");
        return $"msg_{contentHash}_{msg.Content.Length}";
    }

    private static bool MatchesAny(string text, params string[] keywords)
    {
        return keywords.Any(k => text.Contains(k, StringComparison.OrdinalIgnoreCase));
    }
}
