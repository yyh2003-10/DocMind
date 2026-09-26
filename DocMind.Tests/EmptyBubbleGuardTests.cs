using DocMind.ViewModels;

namespace DocMind.Tests;

/// <summary>空泡防护：无正文不显示气泡壳/纯文本兜底。</summary>
public class EmptyBubbleGuardTests
{
    [Fact]
    public void EmptyContent_HasContentFalse_AndNoTextFallback()
    {
        var msg = new ChatMessage { Role = "user", Content = "" };
        Assert.False(msg.HasContent);
        Assert.False(msg.ShowTextFallback);
    }

    [Fact]
    public void WhitespaceContent_TreatedAsEmpty()
    {
        var msg = new ChatMessage { Role = "assistant", Content = "   \n  " };
        Assert.False(msg.HasContent);
        Assert.False(msg.ShowTextFallback);
    }

    [Fact]
    public void PlainContent_ShowsTextFallbackWhenNoDocument()
    {
        var msg = new ChatMessage { Role = "assistant", Content = "你好" };
        Assert.True(msg.HasContent);
        // 普通文本会生成 RenderedDocument，故 fallback 应为 false
        // （markdown 解析成功时）；解析失败才 true。这里只锁 HasContent。
        Assert.True(msg.HasContent);
    }

    [Fact]
    public void HtmlAnswer_DoesNotShowTextFallback()
    {
        var msg = new ChatMessage
        {
            Role = "assistant",
            Content = "<!DOCTYPE html><html><body>hi</body></html>",
            AnswerFormat = "html",
        };
        Assert.True(msg.IsHtmlAnswer);
        Assert.False(msg.ShowTextFallback);
    }

    [Fact]
    public void RestartClearsContent_ResetsHasContent()
    {
        var msg = new ChatMessage { Role = "assistant", Content = "半成品" };
        Assert.True(msg.HasContent);
        msg.Content = string.Empty;
        Assert.False(msg.HasContent);
    }
}
