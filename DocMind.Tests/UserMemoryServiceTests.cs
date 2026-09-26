using DocMind.Services;
using DocMind.Models;

namespace DocMind.Tests;

public class UserMemoryServiceTests : IDisposable
{
    private readonly string _dbPath;
    private readonly UserMemoryService _service;

    public UserMemoryServiceTests()
    {
        _dbPath = Path.Combine(Path.GetTempPath(), $"test_memory_{Guid.NewGuid():N}.db");
        _service = new UserMemoryService(_dbPath);
    }

    public void Dispose()
    {
        try { File.Delete(_dbPath); } catch { }
        try { File.Delete(_dbPath + "-wal"); } catch { }
        try { File.Delete(_dbPath + "-shm"); } catch { }
    }

    [Fact]
    public async Task AddAsync_BasicAddAndRetrieve()
    {
        var added = await _service.AddAsync("用户偏好：使用简洁回答", "user", "manual");
        Assert.True(added);

        var all = await _service.GetAllAsync();
        Assert.Single(all);
        Assert.Equal("用户偏好：使用简洁回答", all[0].Content);
        Assert.Equal("user", all[0].Category);
        Assert.Equal("manual", all[0].Source);
    }

    [Fact]
    public async Task AddAsync_DuplicateIsSkipped()
    {
        await _service.AddAsync("重复内容", "memory");
        var added = await _service.AddAsync("重复内容", "memory");
        Assert.False(added);

        var all = await _service.GetAllAsync();
        Assert.Single(all);
    }

    [Fact]
    public async Task AddAsync_CapacityExceededIsRejected()
    {
        // Fill to near capacity (2190 + 20 > 2200)
        var filler = new string('x', 2190);
        await _service.AddAsync(filler, "memory");

        // This should be rejected (2190 + 20 = 2210 > 2200)
        var added = await _service.AddAsync(new string('y', 20), "memory");
        Assert.False(added);
    }

    [Fact]
    public async Task AddAsync_EmptyContentIsRejected()
    {
        var added = await _service.AddAsync("", "memory");
        Assert.False(added);
        added = await _service.AddAsync("   ", "memory");
        Assert.False(added);
    }

    [Fact]
    public async Task ReplaceAsync_UpdatesContent()
    {
        await _service.AddAsync("旧内容ABC", "memory");
        var replaced = await _service.ReplaceAsync("旧内容", "新内容DEF");
        Assert.True(replaced);

        var all = await _service.GetAllAsync();
        Assert.Single(all);
        Assert.Contains("新内容DEF", all[0].Content);
        Assert.DoesNotContain("旧内容", all[0].Content);
    }

    [Fact]
    public async Task ReplaceAsync_NoMatchReturnsFalse()
    {
        var replaced = await _service.ReplaceAsync("不存在的内容", "新内容");
        Assert.False(replaced);
    }

    [Fact]
    public async Task RemoveAsync_DeletesEntry()
    {
        await _service.AddAsync("要删除的内容", "memory");
        var removed = await _service.RemoveAsync("要删除");
        Assert.True(removed);

        var all = await _service.GetAllAsync();
        Assert.Empty(all);
    }

    [Fact]
    public async Task RemoveAsync_NoMatchReturnsFalse()
    {
        var removed = await _service.RemoveAsync("不存在的内容");
        Assert.False(removed);
    }

    [Fact]
    public async Task GetAllAsync_FilterByCategory()
    {
        await _service.AddAsync("用户偏好", "user");
        await _service.AddAsync("环境信息", "memory");

        var users = await _service.GetAllAsync("user");
        Assert.Single(users);
        Assert.Equal("用户偏好", users[0].Content);

        var memories = await _service.GetAllAsync("memory");
        Assert.Single(memories);
        Assert.Equal("环境信息", memories[0].Content);
    }

    [Fact]
    public async Task SearchAsync_FindsRelevantEntries()
    {
        await _service.AddAsync("用户喜欢用Python开发", "user");
        await _service.AddAsync("项目部署在Docker容器中", "memory");
        await _service.AddAsync("使用VS Code编辑器", "memory");

        var results = await _service.SearchAsync("Python开发");
        Assert.NotEmpty(results);
        Assert.Contains(results, r => r.Entry.Content.Contains("Python"));
    }

    [Fact]
    public async Task SearchAsync_ReturnsEmptyForNoMatch()
    {
        await _service.AddAsync("完全无关的内容", "memory");
        var results = await _service.SearchAsync("量子计算xyz123");
        Assert.Empty(results);
    }

    [Fact]
    public async Task SearchAsync_EmptyQueryReturnsEmpty()
    {
        await _service.AddAsync("测试内容", "memory");
        var results = await _service.SearchAsync("");
        Assert.Empty(results);
    }

    [Fact]
    public async Task BuildSystemPromptInjectionAsync_ReturnsFormattedContext()
    {
        await _service.AddAsync("用户偏好简洁回答", "user");
        await _service.AddAsync("项目使用C#开发", "memory");

        var injection = await _service.BuildSystemPromptInjectionAsync("回答关于项目的问题");
        // 只返回条目列表；[用户记忆] 框定由后端统一注入
        Assert.DoesNotContain("[用户记忆]", injection);
        Assert.Contains("项目", injection);
    }

    [Fact]
    public async Task BuildSystemPromptInjectionAsync_EmptyWhenNoMatch()
    {
        var injection = await _service.BuildSystemPromptInjectionAsync("完全不相关的问题xyz");
        Assert.Equal(string.Empty, injection);
    }

    [Fact]
    public async Task SearchAsync_SingleCjkCharsDoNotMatchEverything()
    {
        // 收紧 FTS：中文单字 OR 不再命中整库；双字/词仍可召回
        await _service.AddAsync("豆包是字节跳动的大模型", "memory");
        await _service.AddAsync("用户喜欢简洁回答", "user");

        var gptHits = await _service.SearchAsync("你知道gpt吗");
        // 无可靠 token 时应接近空结果，不得因为单字「你/知/道」注入豆包记忆
        Assert.DoesNotContain(gptHits, r => r.Entry.Content.Contains("豆包"));

        var doubaoHits = await _service.SearchAsync("介绍一下豆包");
        Assert.Contains(doubaoHits, r => r.Entry.Content.Contains("豆包"));
    }

    [Fact]
    public async Task GetStatsAsync_ReturnsCorrectStats()
    {
        await _service.AddAsync("测试内容ABC", "memory");
        await _service.AddAsync("另一条内容DEF", "user");

        var stats = await _service.GetStatsAsync();
        Assert.Equal(2, stats.EntryCount);
        Assert.True(stats.UsedChars > 0);
        Assert.Equal(UserMemoryService.DefaultMaxChars, stats.MaxChars);
    }

    [Fact]
    public async Task GetStatsAsync_EmptyDatabase()
    {
        var stats = await _service.GetStatsAsync();
        Assert.Equal(0, stats.EntryCount);
        Assert.Equal(0, stats.UsedChars);
    }


    [Fact]
    public async Task DeleteByIdAsync_RemovesSingleEntry()
    {
        await _service.AddAsync("记忆A", "memory");
        await _service.AddAsync("记忆B", "memory");
        var all = await _service.GetAllAsync();
        Assert.Equal(2, all.Count);
        var target = all[0];
        var removed = await _service.DeleteByIdAsync(target.Id);
        Assert.True(removed);
        var after = await _service.GetAllAsync();
        Assert.Single(after);
        Assert.Equal(all[1].Id, after[0].Id);
    }

}
