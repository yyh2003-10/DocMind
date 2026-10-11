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
    public void LooksLikeHtml_PreamblePlusDoctype_ReturnsTrue()
        => Assert.True(HtmlAnswerBubble.LooksLikeHtml("这是要点：\n<!DOCTYPE html><html><body>x</body></html>"));

    [Fact]
    public void LooksLikeHtml_CommentThenHtml_ReturnsTrue()
        => Assert.True(HtmlAnswerBubble.LooksLikeHtml("<!-- page -->\n<html><head></head><body>x</body></html>"));

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

    // ── 主题 CSS：与 Theme.xaml / Theme.Dark.xaml 对齐，深色下覆盖模型硬编码浅色 ──

    [Fact]
    public void BuildThemeCss_Dark_UsesDarkThemeTokens()
    {
        var css = HtmlAnswerBubble.BuildThemeCss(dark: true);
        Assert.Contains("color-scheme: dark", css, StringComparison.Ordinal);
        Assert.Contains("#0B0D11", css, StringComparison.Ordinal); // BackgroundColor
        Assert.Contains("#171C24", css, StringComparison.Ordinal); // CardColor
        Assert.Contains("#F4F7FB", css, StringComparison.Ordinal); // TextPrimaryColor
        Assert.Contains("#818CF8", css, StringComparison.Ordinal); // PrimaryColor
        Assert.DoesNotContain("#FFFFFF", css, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void BuildThemeCss_Light_UsesLightThemeTokens()
    {
        var css = HtmlAnswerBubble.BuildThemeCss(dark: false);
        Assert.Contains("color-scheme: light", css, StringComparison.Ordinal);
        Assert.Contains("#FBFCFE", css, StringComparison.Ordinal);
        Assert.Contains("#1D1D1F", css, StringComparison.Ordinal);
        Assert.Contains("#4F46E5", css, StringComparison.Ordinal);
    }

    [Fact]
    public void BuildThemeCss_HasDocMindVariables()
    {
        var css = HtmlAnswerBubble.BuildThemeCss(dark: true);
        Assert.Contains("--dm-bg", css, StringComparison.Ordinal);
        Assert.Contains("--dm-text", css, StringComparison.Ordinal);
        Assert.Contains("--dm-primary", css, StringComparison.Ordinal);
    }

    [Fact]
    public void IsAppDarkTheme_ReadsResourceWithoutThrowing()
    {
        // 无 Application 时应返回 false，不抛异常
        var _ = HtmlAnswerBubble.IsAppDarkTheme();
    }
}
