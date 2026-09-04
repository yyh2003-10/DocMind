using System.IO;
using System.Text.Json;
using DocMind.Models;

namespace DocMind.Services;

/// <summary>
/// 操作检查点服务：长时间操作前保存状态，中断后从断点恢复。
/// 灵感来源：Hive 框架的 crash-safe park/resume 模式。
///
/// 典型场景：
/// - OCR/嵌入 500 个文档 → 进度 60% → 应用崩溃 → 重新来过
/// - 批量导入 → 中途失败 → 需要手动记住断点
///
/// 设计要点：
/// - JSON 序列化状态到磁盘（%LOCALAPPDATA%/DocMind/checkpoints/）
/// - 操作完成后自动清除检查点
/// - 支持多操作并行（每个操作有独立 ID）
/// - 启动时检查未完成操作
/// </summary>
public sealed class CheckpointService
{
    private readonly string _checkpointDir;

    public CheckpointService()
    {
        var configDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "DocMind");
        _checkpointDir = Path.Combine(configDir, "checkpoints");
        Directory.CreateDirectory(_checkpointDir);
    }

    /// <summary>用于单元测试的临时路径构造。</summary>
    internal CheckpointService(string checkpointDir)
    {
        _checkpointDir = checkpointDir;
        Directory.CreateDirectory(_checkpointDir);
    }

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = false,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    /// <summary>保存检查点。</summary>
    public Task SaveCheckpointAsync(CheckpointState state)
    {
        var path = GetCheckpointPath(state.OperationId);
        var tmpPath = path + ".tmp";
        var json = JsonSerializer.Serialize(state, JsonOpts);
        File.WriteAllText(tmpPath, json);
        // 原子替换：写临时文件后 rename，避免写入中途崩溃导致 JSON 损坏
        File.Move(tmpPath, path, overwrite: true);
        DebugLog.Info($"检查点已保存: {state.OperationId} ({state.OperationType}, {state.CompletedItems}/{state.TotalItems})", "Checkpoint");
        return Task.CompletedTask;
    }

    /// <summary>恢复检查点。返回 null 表示无检查点。</summary>
    public Task<CheckpointState?> RestoreCheckpointAsync(string operationId)
    {
        var path = GetCheckpointPath(operationId);
        if (!File.Exists(path))
            return Task.FromResult<CheckpointState?>(null);

        try
        {
            var json = File.ReadAllText(path);
            var state = JsonSerializer.Deserialize<CheckpointState>(json, JsonOpts);
            DebugLog.Info($"检查点已恢复: {operationId} ({state?.OperationType}, {state?.CompletedItems}/{state?.TotalItems})", "Checkpoint");
            return Task.FromResult(state);
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"检查点恢复失败: {operationId} — {ex.Message}", "Checkpoint");
            return Task.FromResult<CheckpointState?>(null);
        }
    }

    /// <summary>清除检查点（操作完成后调用）。</summary>
    public Task ClearCheckpointAsync(string operationId)
    {
        var path = GetCheckpointPath(operationId);
        if (File.Exists(path))
        {
            File.Delete(path);
            DebugLog.Info($"检查点已清除: {operationId}", "Checkpoint");
        }
        return Task.CompletedTask;
    }

    /// <summary>列出所有未完成的检查点。</summary>
    public Task<IReadOnlyList<CheckpointState>> ListPendingCheckpointsAsync()
    {
        var results = new List<CheckpointState>();
        if (!Directory.Exists(_checkpointDir))
            return Task.FromResult<IReadOnlyList<CheckpointState>>(results);

        foreach (var file in Directory.GetFiles(_checkpointDir, "*.json"))
        {
            try
            {
                var json = File.ReadAllText(file);
                var state = JsonSerializer.Deserialize<CheckpointState>(json, JsonOpts);
                if (state is not null)
                    results.Add(state);
            }
            catch { /* 跳过损坏的检查点文件 */ }
        }
        return Task.FromResult<IReadOnlyList<CheckpointState>>(results);
    }

    private string GetCheckpointPath(string operationId)
    {
        // 清理文件名中的非法字符
        var safeId = string.Join("_", operationId.Split(Path.GetInvalidFileNameChars()));
        return Path.Combine(_checkpointDir, $"{safeId}.json");
    }
}

/// <summary>
/// 检查点状态：序列化到磁盘的操作进度快照。
/// </summary>
public sealed class CheckpointState
{
    /// <summary>操作唯一标识（如 "ingest_batch_20260903"）。</summary>
    public string OperationId { get; set; } = string.Empty;

    /// <summary>操作类型（"ingest" | "ocr" | "reindex" | "curate" | "download_model"）。</summary>
    public string OperationType { get; set; } = string.Empty;

    /// <summary>已完成项目数。</summary>
    public int CompletedItems { get; set; }

    /// <summary>总项目数。</summary>
    public int TotalItems { get; set; }

    /// <summary>已处理的文件路径列表。</summary>
    public List<string> ProcessedFiles { get; set; } = new();

    /// <summary>检查点保存时间。</summary>
    public DateTime SavedAt { get; set; } = DateTime.UtcNow;

    /// <summary>失败时的错误上下文（可选）。</summary>
    public string? ErrorContext { get; set; }

    /// <summary>额外元数据（键值对）。</summary>
    public Dictionary<string, string> Metadata { get; set; } = new();

    /// <summary>进度百分比。</summary>
    public double ProgressPercent => TotalItems > 0 ? (double)CompletedItems / TotalItems * 100 : 0;
}
