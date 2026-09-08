namespace DocMind.Tests;

using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Windows.Documents;
using DocMind.Models;
using DocMind.ViewModels;
using Xunit;

[Collection("SettingsFile")]
public class GroundTruthViewerTests
{
    private static ChatViewModel CreateVm(FakeDoc2kbApiService? fake = null)
    {
        fake ??= new FakeDoc2kbApiService();
        return new ChatViewModel(fake, null, new AppSettings { LlmProvider = "openai" });
    }

    [Fact]
    public void SourceDrawerWidth_AllowsUpTo1100Px()
    {
        var vm = CreateVm();
        vm.SourceDrawerWidth = 1000;
        Assert.Equal(1000, vm.SourceDrawerWidth);

        // 超过 1100 会被钳制在 1100
        vm.SourceDrawerWidth = 1500;
        Assert.Equal(ChatViewModel.MaxSourceDrawerWidth, vm.SourceDrawerWidth);
        Assert.Equal(1100, ChatViewModel.MaxSourceDrawerWidth);
    }

    [Fact]
    public void OpenSource_WhenPdfAndNarrowDrawer_ExpandsWidthToComfortableReading()
    {
        var vm = CreateVm();
        vm.SourceDrawerWidth = 360; // 较窄

        var pdfSource = new SourceRef
        {
            Index = 1,
            Source = @"C:\docs\manual.pdf",
            Format = "pdf",
            Page = 67,
            Snippet = "这是第67页的引用内容",
        };

        vm.OpenSourceCommand.Execute(pdfSource);

        Assert.True(vm.IsSourceDrawerOpen);
        Assert.False(vm.IsArtifactMode);
        Assert.Equal(pdfSource, vm.SelectedSource);
        // 宽度应自适应提升至 560px
        Assert.Equal(560, vm.SourceDrawerWidth);
    }

    [Fact]
    public void OpenSource_WhenNonPdf_PreservesOriginalDrawerWidth()
    {
        var vm = CreateVm();
        vm.SourceDrawerWidth = 360;

        var txtSource = new SourceRef
        {
            Index = 1,
            Source = @"C:\docs\notes.txt",
            Format = "txt",
            Page = 1,
            Snippet = "纯文本切片",
        };

        vm.OpenSourceCommand.Execute(txtSource);

        Assert.True(vm.IsSourceDrawerOpen);
        Assert.Equal(360, vm.SourceDrawerWidth);
    }

    [Fact]
    public void SourceMarkers_WhenPdfSource_RendersCitationHyperlink()
    {
        RunOnSta(() =>
        {
            var msg = new ChatMessage { Role = "assistant" };
            msg.Sources = new List<SourceRef>
            {
                new()
                {
                    Index = 1,
                    Source = @"C:\docs\specification.pdf",
                    Format = "pdf",
                    Page = 42,
                    Snippet = "这是第42页技术规范内容",
                },
            };

            msg.Content = "根据相关规范[1]说明，参数应在安全阈值内。";

            Assert.NotNull(msg.RenderedDocument);
            var links = FindHyperlinks(msg.RenderedDocument!).Where(l => l.NavigateUri is null).ToList();
            Assert.Single(links);

            var link = links[0];
            Assert.NotNull(link.ToolTip);
            // 验证角标文本中包含数字索引与文档标识
            var markerText = string.Concat(link.Inlines.OfType<Run>().Select(r => r.Text));
            Assert.Contains("1", markerText);
        });
    }

    private static void RunOnSta(Action action)
    {
        Exception? error = null;
        var thread = new Thread(() =>
        {
            try
            {
                action();
            }
            catch (Exception ex)
            {
                error = ex;
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (error is not null)
        {
            throw new Xunit.Sdk.XunitException($"STA 线程断言失败: {error}", error);
        }
    }

    private static IEnumerable<Hyperlink> FindHyperlinks(FlowDocument doc)
        => doc.Blocks.SelectMany(FindHyperlinks);

    private static IEnumerable<Hyperlink> FindHyperlinks(Block block) => block switch
    {
        Paragraph p => p.Inlines.SelectMany(FindHyperlinks),
        Section s => s.Blocks.SelectMany(FindHyperlinks),
        List l => l.ListItems.SelectMany(i => i.Blocks.SelectMany(FindHyperlinks)),
        Table t => t.RowGroups.SelectMany(rg => rg.Rows.SelectMany(r => r.Cells.SelectMany(c => c.Blocks.SelectMany(FindHyperlinks)))),
        _ => Enumerable.Empty<Hyperlink>(),
    };

    private static IEnumerable<Hyperlink> FindHyperlinks(Inline inline) => inline switch
    {
        Hyperlink h => new[] { h },
        Span s => s.Inlines.SelectMany(FindHyperlinks),
        _ => Enumerable.Empty<Hyperlink>(),
    };
}
