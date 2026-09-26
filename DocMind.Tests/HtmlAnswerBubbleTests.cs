using DocMind.Controls;
using Xunit;

namespace DocMind.Tests;

/// <summary>HTML 体验气泡：内容探测与围栏剥离的单元测试。</summary>
public sealed class HtmlAnswerBubbleTests
{
    [Fact]
    public void LooksLikeHtml_Doctype_ReturnsTrue()
        => Assert.True(HtmlAnswerBubble.LooksLikeHtml("<!DOCTYPE html><html><body>hi</body></html>"));

    [Fact]
    public void LooksLikeHtml_HtmlTag_ReturnsTrue()
        => Assert.True(HtmlAnswerBubble.LooksLikeHtml("<html lang=\"zh\"><body>x</body></html>"));

    [Fact]
    public void LooksLikeHtml_FencedHtml_ReturnsTrue()
        => Assert.True(HtmlAnswerBubble.LooksLikeHtml("```html\n<!DOCTYPE html><html></html>\n```"));

    [Fact]
    public void LooksLikeHtml_Markdown_ReturnsFalse()
        => Assert.False(HtmlAnswerBubble.LooksLikeHtml("# 标题\n\n正文 **加粗** 与 `代码`。"));

    [Fact]
    public void LooksLikeHtml_InlineHtmlFragment_ReturnsFalse()
        => Assert.False(HtmlAnswerBubble.LooksLikeHtml("这是一段 <b>行内</b> 标签，不是整页 HTML。"));

    [Fact]
    public void LooksLikeHtml_NullOrEmpty_ReturnsFalse()
    {
        Assert.False(HtmlAnswerBubble.LooksLikeHtml(null));
        Assert.False(HtmlAnswerBubble.LooksLikeHtml(""));
        Assert.False(HtmlAnswerBubble.LooksLikeHtml("   "));
    }

    [Fact]
    public void StripHtmlFence_RemovesFence()
    {
        const string fenced = "```html\n<!DOCTYPE html><html><body>x</body></html>\n```";
        var stripped = HtmlAnswerBubble.StripHtmlFence(fenced);
        Assert.StartsWith("<!DOCTYPE", stripped, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("```", stripped, StringComparison.Ordinal);
    }

    [Fact]
    public void StripHtmlFence_PlainHtml_Unchanged()
    {
        const string page = "<!DOCTYPE html><html><body>x</body></html>";
        Assert.Equal(page, HtmlAnswerBubble.StripHtmlFence(page));
    }

    [Fact]
    public void StripHtmlFence_Empty_ReturnsEmpty()
        => Assert.Equal(string.Empty, HtmlAnswerBubble.StripHtmlFence("   "));

    // ── 收集中预览：去标签，避免把半截 HTML 直接交给 WebView2 ──────────

    [Fact]
    public void BuildPreview_StripsTagsScriptAndStyle()
    {
        const string html =
            "<!DOCTYPE html><html><head><style>.a{color:red}</style></head>"
            + "<body><h1>标题</h1><p>正文<strong>加粗</strong></p>"
            + "<script>var x=1;</script></body></html>";
        var preview = HtmlAnswerBubble.BuildPreview(html);
        Assert.Contains("标题", preview, StringComparison.Ordinal);
        Assert.Contains("正文", preview, StringComparison.Ordinal);
        Assert.Contains("加粗", preview, StringComparison.Ordinal);
        Assert.DoesNotContain("<", preview, StringComparison.Ordinal);
        Assert.DoesNotContain("var x=1", preview, StringComparison.Ordinal);
        Assert.DoesNotContain("color:red", preview, StringComparison.Ordinal);
    }

    [Fact]
    public void BuildPreview_UnclosedScript_StillStripped()
    {
        // 流式中常见：<script> 还没闭合，此时更要把脚本内容挡在预览外
        const string html = "<html><body>可见内容<script>var secret=1;";
        var preview = HtmlAnswerBubble.BuildPreview(html);
        Assert.Contains("可见内容", preview, StringComparison.Ordinal);
        Assert.DoesNotContain("secret", preview, StringComparison.Ordinal);
    }

    [Fact]
    public void BuildPreview_FencedHtml_StripsFence()
    {
        const string fenced = "```html\n<!DOCTYPE html><html><body>围栏正文</body></html>\n```";
        var preview = HtmlAnswerBubble.BuildPreview(fenced);
        Assert.Contains("围栏正文", preview, StringComparison.Ordinal);
        Assert.DoesNotContain("```", preview, StringComparison.Ordinal);
    }

    [Fact]
    public void BuildPreview_TailTruncation_KeepsLatest()
    {
        var html = "<p>" + new string('a', 500) + "最新尾部</p>";
        var preview = HtmlAnswerBubble.BuildPreview(html, maxChars: 20);
        Assert.StartsWith("…", preview, StringComparison.Ordinal);
        Assert.Contains("最新尾部", preview, StringComparison.Ordinal);
        Assert.Equal(21, preview.Length);
    }

    [Fact]
    public void BuildPreview_NullOrEmpty_ReturnsEmpty()
    {
        Assert.Equal(string.Empty, HtmlAnswerBubble.BuildPreview(null));
        Assert.Equal(string.Empty, HtmlAnswerBubble.BuildPreview("   "));
        Assert.Equal(string.Empty, HtmlAnswerBubble.BuildPreview("<html></html>"));
    }


    [Fact]
    public void HtmlToMarkdown_HeadingsBoldCite_ReturnsMarkdown()
    {
        const string html = """
            <!DOCTYPE html><html><head><style>.x{}</style><script>bad()</script></head>
            <body>
            <h1>AI 大模型</h1>
            <p>核心是 <strong>Transformer</strong>，见 <a class="cite" data-n="1">[1]</a>。</p>
            <ul><li>Self-Attention</li><li>FFN</li></ul>
            </body></html>
            """;
        var md = HtmlAnswerBubble.HtmlToMarkdown(html);
        Assert.Contains("# AI 大模型", md, StringComparison.Ordinal);
        Assert.Contains("**Transformer**", md, StringComparison.Ordinal);
        Assert.Contains("[1]", md, StringComparison.Ordinal);
        Assert.Contains("- Self-Attention", md, StringComparison.Ordinal);
        Assert.DoesNotContain("script", md, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("<h1", md, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void HtmlToMarkdown_FencedHtml_StripsFenceFirst()
    {
        const string fenced = "```html\n<h2>标题</h2>\n<p>正文</p>\n```";
        var md = HtmlAnswerBubble.HtmlToMarkdown(fenced);
        Assert.Contains("# 标题", md, StringComparison.Ordinal);
        Assert.DoesNotContain("```", md, StringComparison.Ordinal);
    }

    [Fact]
    public void HtmlToMarkdown_NullOrEmpty_ReturnsEmpty()
    {
        Assert.Equal(string.Empty, HtmlAnswerBubble.HtmlToMarkdown(null));
        Assert.Equal(string.Empty, HtmlAnswerBubble.HtmlToMarkdown("   "));
    }
}
