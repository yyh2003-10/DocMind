using DocMind.ViewModels;

namespace DocMind.Tests;

/// <summary>消息操作行：赞/踩必须写入 FeedbackSink（落库），不再只是本地布尔翻转。</summary>
public class MessageActionFeedbackTests
{
    [Fact]
    public async Task ToggleLike_WhenTurnedOn_InvokesFeedbackSinkWithTrue()
    {
        var msg = new ChatMessage { Role = "assistant", Content = "回答" };
        var calls = new List<bool>();
        msg.FeedbackSink = (_, isLike) =>
        {
            calls.Add(isLike);
            return Task.CompletedTask;
        };

        await msg.ToggleLikeCommand.ExecuteAsync(null);

        Assert.True(msg.IsLiked);
        Assert.Single(calls);
        Assert.True(calls[0]);
    }

    [Fact]
    public async Task ToggleLike_WhenTurnedOff_DoesNotInvokeFeedbackSink()
    {
        var msg = new ChatMessage { Role = "assistant", Content = "回答", IsLiked = true };
        var calls = new List<bool>();
        msg.FeedbackSink = (_, isLike) =>
        {
            calls.Add(isLike);
            return Task.CompletedTask;
        };

        await msg.ToggleLikeCommand.ExecuteAsync(null);

        Assert.False(msg.IsLiked);
        Assert.Empty(calls);
    }

    [Fact]
    public async Task ToggleDislike_WhenTurnedOn_InvokesFeedbackSinkWithFalse()
    {
        var msg = new ChatMessage { Role = "assistant", Content = "回答" };
        var calls = new List<bool>();
        msg.FeedbackSink = (_, isLike) =>
        {
            calls.Add(isLike);
            return Task.CompletedTask;
        };

        await msg.ToggleDislikeCommand.ExecuteAsync(null);

        Assert.True(msg.IsDisliked);
        Assert.Single(calls);
        Assert.False(calls[0]);
    }

    [Fact]
    public async Task ToggleLike_MutuallyExcludesDislike()
    {
        var msg = new ChatMessage { Role = "assistant", Content = "回答", IsDisliked = true };
        msg.FeedbackSink = (_, _) => Task.CompletedTask;

        await msg.ToggleLikeCommand.ExecuteAsync(null);

        Assert.True(msg.IsLiked);
        Assert.False(msg.IsDisliked);
    }

    [Fact]
    public async Task ToggleLike_WithoutFeedbackSink_DoesNotThrow()
    {
        var msg = new ChatMessage { Role = "assistant", Content = "回答" };

        await msg.ToggleLikeCommand.ExecuteAsync(null);

        Assert.True(msg.IsLiked);
    }
}
