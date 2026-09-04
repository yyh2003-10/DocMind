using DocMind.Services;

namespace DocMind.Tests;

public class FeedbackServiceTests : IDisposable
{
    private readonly string _dbPath;
    private readonly FeedbackService _service;

    public FeedbackServiceTests()
    {
        _dbPath = Path.Combine(Path.GetTempPath(), $"test_feedback_{Guid.NewGuid():N}.db");
        _service = new FeedbackService(_dbPath);
    }

    public void Dispose()
    {
        try { File.Delete(_dbPath); } catch { }
        try { File.Delete(_dbPath + "-wal"); } catch { }
        try { File.Delete(_dbPath + "-shm"); } catch { }
    }

    [Fact]
    public async Task SubmitFeedbackAsync_BasicUpVote()
    {
        var result = await _service.SubmitFeedbackAsync("msg_001", "up",
            chatId: "chat1", querySnapshot: "什么是知识库", responseSnapshot: "知识库是...");
        Assert.True(result);

        var stats = await _service.GetStatsAsync();
        Assert.Equal(1, stats.TotalFeedbacks);
        Assert.Equal(1, stats.UpCount);
        Assert.Equal(0, stats.DownCount);
    }

    [Fact]
    public async Task SubmitFeedbackAsync_BasicDownVote()
    {
        var result = await _service.SubmitFeedbackAsync("msg_002", "down",
            correction: "回答不够详细");
        Assert.True(result);

        var stats = await _service.GetStatsAsync();
        Assert.Equal(1, stats.DownCount);
    }

    [Fact]
    public async Task SubmitFeedbackAsync_InvalidRating_ReturnsFalse()
    {
        var result = await _service.SubmitFeedbackAsync("msg_003", "neutral");
        Assert.False(result);
    }

    [Fact]
    public async Task SubmitFeedbackAsync_EmptyMessageId_ReturnsFalse()
    {
        var result = await _service.SubmitFeedbackAsync("", "up");
        Assert.False(result);
    }

    [Fact]
    public async Task GetFeedbacksAsync_FilterByChatId()
    {
        await _service.SubmitFeedbackAsync("msg_1", "up", chatId: "chat_a");
        await _service.SubmitFeedbackAsync("msg_2", "down", chatId: "chat_b");
        await _service.SubmitFeedbackAsync("msg_3", "up", chatId: "chat_a");

        var chatA = await _service.GetFeedbacksAsync(chatId: "chat_a");
        Assert.Equal(2, chatA.Count);

        var chatB = await _service.GetFeedbacksAsync(chatId: "chat_b");
        Assert.Single(chatB);
    }

    [Fact]
    public async Task GetFeedbacksAsync_NoFilter_ReturnsAll()
    {
        await _service.SubmitFeedbackAsync("msg_1", "up");
        await _service.SubmitFeedbackAsync("msg_2", "down");

        var all = await _service.GetFeedbacksAsync();
        Assert.Equal(2, all.Count);
    }

    [Fact]
    public async Task GetStatsAsync_EmptyDatabase()
    {
        var stats = await _service.GetStatsAsync();
        Assert.Equal(0, stats.TotalFeedbacks);
        Assert.Equal(0, stats.UpCount);
        Assert.Equal(0, stats.DownCount);
        Assert.Equal(0, stats.SatisfactionRate);
    }

    [Fact]
    public async Task GetStatsAsync_SatisfactionRate()
    {
        await _service.SubmitFeedbackAsync("msg_1", "up");
        await _service.SubmitFeedbackAsync("msg_2", "up");
        await _service.SubmitFeedbackAsync("msg_3", "down");

        var stats = await _service.GetStatsAsync();
        Assert.Equal(3, stats.TotalFeedbacks);
        Assert.InRange(stats.SatisfactionRate, 0.66, 0.67); // 2/3
    }

    [Fact]
    public async Task ShouldAnalyzeAsync_BelowThreshold()
    {
        await _service.SubmitFeedbackAsync("msg_1", "up");
        Assert.False(await _service.ShouldAnalyzeAsync());
    }

    [Fact]
    public async Task ShouldAnalyzeAsync_AtThreshold()
    {
        for (int i = 0; i < FeedbackService.AutoAnalyzeThreshold; i++)
        {
            await _service.SubmitFeedbackAsync($"msg_{i}", i % 2 == 0 ? "up" : "down");
        }
        Assert.True(await _service.ShouldAnalyzeAsync());
    }

    [Fact]
    public async Task AnalyzeAndStoreInsightsAsync_ExtractsCorrections()
    {
        // Add enough down feedbacks with corrections
        for (int i = 0; i < 3; i++)
        {
            await _service.SubmitFeedbackAsync($"msg_down_{i}", "down",
                correction: "回答太简短了，请更详细");
        }
        // Add some up feedbacks to reach threshold
        for (int i = 0; i < 3; i++)
        {
            await _service.SubmitFeedbackAsync($"msg_up_{i}", "up",
                querySnapshot: "如何使用Python");
        }

        var memoryPath = Path.Combine(Path.GetTempPath(), $"test_mem_{Guid.NewGuid():N}.db");
        var memory = new UserMemoryService(memoryPath);
        try
        {
            var added = await _service.AnalyzeAndStoreInsightsAsync(memory);
            Assert.True(added > 0);

            // Verify memory was populated
            var all = await memory.GetAllAsync();
            Assert.NotEmpty(all);
            Assert.Contains(all, e => e.Source == "feedback");
        }
        finally
        {
            memory.Dispose();
            try { File.Delete(memoryPath); } catch { }
            try { File.Delete(memoryPath + "-wal"); } catch { }
            try { File.Delete(memoryPath + "-shm"); } catch { }
        }
    }
}
