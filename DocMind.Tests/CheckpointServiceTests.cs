using DocMind.Services;
using DocMind.Models;

namespace DocMind.Tests;

public class CheckpointServiceTests : IDisposable
{
    private readonly string _checkpointDir;
    private readonly CheckpointService _service;

    public CheckpointServiceTests()
    {
        _checkpointDir = Path.Combine(Path.GetTempPath(), $"test_checkpoints_{Guid.NewGuid():N}");
        _service = new CheckpointService(_checkpointDir);
    }

    public void Dispose()
    {
        try { Directory.Delete(_checkpointDir, recursive: true); } catch { }
    }

    [Fact]
    public async Task SaveAndRestoreCheckpoint()
    {
        var state = new CheckpointState
        {
            OperationId = "test_op_1",
            OperationType = "ingest",
            CompletedItems = 10,
            TotalItems = 50,
            ProcessedFiles = new List<string> { "file1.pdf", "file2.pdf" },
            Metadata = new Dictionary<string, string>
            {
                ["path"] = "/docs/test",
                ["collection"] = "default"
            }
        };

        await _service.SaveCheckpointAsync(state);
        var restored = await _service.RestoreCheckpointAsync("test_op_1");

        Assert.NotNull(restored);
        Assert.Equal("ingest", restored.OperationType);
        Assert.Equal(10, restored.CompletedItems);
        Assert.Equal(50, restored.TotalItems);
        Assert.Equal(2, restored.ProcessedFiles.Count);
        Assert.Equal("/docs/test", restored.Metadata["path"]);
        Assert.Equal("default", restored.Metadata["collection"]);
    }

    [Fact]
    public async Task RestoreCheckpoint_NonExistentReturnsNull()
    {
        var result = await _service.RestoreCheckpointAsync("nonexistent_op");
        Assert.Null(result);
    }

    [Fact]
    public async Task ClearCheckpoint_DeletesFile()
    {
        var state = new CheckpointState
        {
            OperationId = "to_delete",
            OperationType = "ocr"
        };

        await _service.SaveCheckpointAsync(state);
        var before = await _service.RestoreCheckpointAsync("to_delete");
        Assert.NotNull(before);

        await _service.ClearCheckpointAsync("to_delete");
        var after = await _service.RestoreCheckpointAsync("to_delete");
        Assert.Null(after);
    }

    [Fact]
    public async Task ListPendingCheckpoints_ReturnsAll()
    {
        await _service.SaveCheckpointAsync(new CheckpointState
        {
            OperationId = "op1",
            OperationType = "ingest"
        });
        await _service.SaveCheckpointAsync(new CheckpointState
        {
            OperationId = "op2",
            OperationType = "reindex"
        });

        var pending = await _service.ListPendingCheckpointsAsync();
        Assert.Equal(2, pending.Count);
        Assert.Contains(pending, p => p.OperationId == "op1");
        Assert.Contains(pending, p => p.OperationId == "op2");
    }

    [Fact]
    public async Task ListPendingCheckpoints_EmptyDir()
    {
        var pending = await _service.ListPendingCheckpointsAsync();
        Assert.Empty(pending);
    }

    [Fact]
    public async Task SaveCheckpoint_AtomicWrite_NoCorruption()
    {
        // Verify that writing twice doesn't leave corrupt files
        var state = new CheckpointState
        {
            OperationId = "atomic_test",
            OperationType = "ingest",
            CompletedItems = 5
        };

        await _service.SaveCheckpointAsync(state);
        state.CompletedItems = 10;
        await _service.SaveCheckpointAsync(state);

        var restored = await _service.RestoreCheckpointAsync("atomic_test");
        Assert.NotNull(restored);
        Assert.Equal(10, restored.CompletedItems); // Should be latest value
    }

    [Fact]
    public async Task RestoreCheckpoint_CorruptFileReturnsNull()
    {
        // Manually write corrupt JSON to simulate crash during write
        var path = Path.Combine(_checkpointDir, "corrupt_op.json");
        await File.WriteAllTextAsync(path, "{invalid json!!!");

        var result = await _service.RestoreCheckpointAsync("corrupt_op");
        Assert.Null(result);
    }

    [Fact]
    public void CheckpointState_ProgressPercent_CalculatesCorrectly()
    {
        var state = new CheckpointState
        {
            CompletedItems = 25,
            TotalItems = 100
        };
        Assert.Equal(25.0, state.ProgressPercent);

        state.CompletedItems = 0;
        state.TotalItems = 0;
        Assert.Equal(0.0, state.ProgressPercent);
    }
}
