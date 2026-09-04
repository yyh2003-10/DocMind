using DocMind.Services;

namespace DocMind.Tests;

public class ToolCallResilienceTests
{
    [Fact]
    public async Task SearchWithHealingAsync_ReturnsSuccessfulResult()
    {
        var callCount = 0;
        var result = await ToolCallResilience.SearchWithHealingAsync<string>(
            searchFn: (topK, minScore) =>
            {
                callCount++;
                return Task.FromResult("search-result");
            },
            query: "test query",
            topK: 5,
            minScore: 0.5,
            maxRetries: 2);

        Assert.Equal("search-result", result);
        Assert.Equal(1, callCount); // Only called once on success
    }

    [Fact]
    public async Task SearchWithHealingAsync_RetriesOnException()
    {
        var callCount = 0;
        var result = await ToolCallResilience.SearchWithHealingAsync<string>(
            searchFn: (topK, minScore) =>
            {
                callCount++;
                if (callCount == 1)
                    throw new Exception("transient error");
                return Task.FromResult("recovered");
            },
            query: "test query",
            topK: 5,
            minScore: 0.5,
            maxRetries: 2);

        Assert.Equal("recovered", result);
        Assert.Equal(2, callCount); // First call failed, second succeeded
    }

    [Fact]
    public async Task SearchWithHealingAsync_ExhaustsRetriesAndThrows()
    {
        var callCount = 0;
        await Assert.ThrowsAsync<Exception>(async () =>
        {
            await ToolCallResilience.SearchWithHealingAsync<string>(
                searchFn: (topK, minScore) =>
                {
                    callCount++;
                    throw new Exception("persistent error");
                },
                query: "test query",
                topK: 5,
                minScore: 0.5,
                maxRetries: 2);
        });

        Assert.Equal(3, callCount); // Initial + 2 retries = 3 total
    }

    [Fact]
    public async Task ExecuteWithHealingAsync_RetriesWithBackoff()
    {
        var callCount = 0;
        var result = await ToolCallResilience.ExecuteWithHealingAsync(
            operation: () =>
            {
                callCount++;
                if (callCount == 1)
                    throw new InvalidOperationException("fail once");
                return Task.FromResult(42);
            },
            operationName: "test-op",
            maxRetries: 2);

        Assert.Equal(42, result);
        Assert.Equal(2, callCount);
    }

    [Fact]
    public async Task ExecuteWithHealingAsync_ShouldRetryFalse_StopsImmediately()
    {
        var callCount = 0;
        await Assert.ThrowsAsync<InvalidOperationException>(async () =>
        {
            await ToolCallResilience.ExecuteWithHealingAsync<bool>(
                operation: () =>
                {
                    callCount++;
                    throw new InvalidOperationException("auth error");
                },
                operationName: "test-op",
                maxRetries: 2,
                shouldRetry: ex => false); // Don't retry auth errors
        });

        Assert.Equal(1, callCount); // Only called once
    }

    [Fact]
    public void IsEmptySearchResult_TrueForEmptyList()
    {
        var empty = new List<string>();
        Assert.True(ToolCallResilience.IsEmptySearchResult(empty, l => l.Count));
    }

    [Fact]
    public void IsEmptySearchResult_FalseForNonEmptyList()
    {
        var nonEmpty = new List<string> { "item" };
        Assert.False(ToolCallResilience.IsEmptySearchResult(nonEmpty, l => l.Count));
    }
}
