using DocMind.Models;
using DocMind.ViewModels;
using Xunit;

namespace DocMind.Tests;

public class CreativeArtifactTests
{
    [Fact]
    public void ChatMessage_ExtractsArtifactAndPptSlidesWithArchetypes()
    {
        var msg = new ChatMessage
        {
            Role = "assistant"
        };

        var text = @"
:::artifact type=""pptx"" title=""DocMind 架构深度汇报""
---
# 封面标题
## 本地智能优先
<!-- note: 各位领导好，这是开场 -->
---
<!-- layout: cards -->
# 核心架构分层
### 向量计算层
- CPU 轻量嵌入
### 存储与图谱
- SQLite 原生存储
---
<!-- layout: metrics -->
# 关键性能指标
- 99.9% : 服务可用性
- 10x : 检索吞吐提升
---
<!-- layout: timeline -->
# 实施路线图
- 阶段一 : 架构规划
- 阶段二 : 落地投产
:::
";
        msg.AppendToken(text);
        msg.ForceRefreshRender();

        Assert.True(msg.HasArtifact);
        Assert.NotNull(msg.Artifact);
        Assert.Equal("DocMind 架构深度汇报", msg.Artifact.Title);
        Assert.True(msg.Artifact.IsPpt);
        Assert.Equal(4, msg.Artifact.SlideCount);

        // Slide 1: Cover
        var s1 = msg.Artifact.Slides[0];
        Assert.True(s1.IsCover);
        Assert.Equal("封面标题", s1.Title);

        // Slide 2: Cards
        var s2 = msg.Artifact.Slides[1];
        Assert.True(s2.IsCards);
        Assert.Equal(2, s2.Cards.Count);
        Assert.Equal("向量计算层", s2.Cards[0].Title);

        // Slide 3: Metrics
        var s3 = msg.Artifact.Slides[2];
        Assert.True(s3.IsMetrics);
        Assert.Equal(2, s3.Metrics.Count);
        Assert.Equal("99.9%", s3.Metrics[0].Value);

        // Slide 4: Timeline
        var s4 = msg.Artifact.Slides[3];
        Assert.True(s4.IsTimeline);
        Assert.Equal(2, s4.TimelineNodes.Count);
        Assert.Equal("阶段一", s4.TimelineNodes[0].Stage);
    }

    [Theory]
    [InlineData("ppt", "ppt")]
    [InlineData("report", "doc")]
    [InlineData("lesson", "lesson")]
    [InlineData("matrix", "table")]
    [InlineData("webpage", "web")]
    public void ChatViewModel_PromptTemplate_SwitchesCreativePersona(string templateType, string expectedPersonaId)
    {
        var fakeApi = new FakeDoc2kbApiService();
        var vm = new ChatViewModel(fakeApi);

        vm.InsertPromptTemplateCommand.Execute(templateType);

        Assert.False(string.IsNullOrWhiteSpace(vm.InputText));
        Assert.Equal(expectedPersonaId, vm.SelectedPersona.Id);
    }

    [Fact]
    public void ChatViewModel_ArtifactSlidePaging_Works()
    {
        var fakeApi = new FakeDoc2kbApiService();
        var vm = new ChatViewModel(fakeApi);

        var artifact = new ArtifactItem
        {
            Type = "pptx",
            Title = "翻页测试",
            RawContent = "test",
            Slides = new List<SlideItem>
            {
                new() { Index = 1, Title = "第 1 页" },
                new() { Index = 2, Title = "第 2 页" },
                new() { Index = 3, Title = "第 3 页" },
            }
        };

        vm.OpenArtifactCommand.Execute(artifact);

        Assert.True(vm.IsArtifactMode);
        Assert.True(vm.IsSourceDrawerOpen);
        Assert.Equal(0, vm.CurrentSlideIndex);
        Assert.Equal("1 / 3", vm.SlideCountText);
        Assert.False(vm.CanPrevSlide);
        Assert.True(vm.CanNextSlide);

        // 下一页
        vm.NextSlideCommand.Execute(null);
        Assert.Equal(1, vm.CurrentSlideIndex);
        Assert.Equal("2 / 3", vm.SlideCountText);
        Assert.True(vm.CanPrevSlide);
        Assert.True(vm.CanNextSlide);

        // 最后一页
        vm.NextSlideCommand.Execute(null);
        Assert.Equal(2, vm.CurrentSlideIndex);
        Assert.Equal("3 / 3", vm.SlideCountText);
        Assert.False(vm.CanNextSlide);

        // 上一页
        vm.PrevSlideCommand.Execute(null);
        Assert.Equal(1, vm.CurrentSlideIndex);
    }

    [Fact]
    public async Task ChatViewModel_ExportArtifact_WithTheme_CallsApi()
    {
        var fakeApi = new FakeDoc2kbApiService();
        var apiCalled = false;
        fakeApi.OnExportCreativeArtifact = (req, ct) =>
        {
            apiCalled = true;
            Assert.Equal("pptx", req.Format);
            Assert.Equal("导出测试", req.Title);
            Assert.Equal("emerald_green", req.Theme);
            return Task.FromResult(new CreativeExportResponse
            {
                Ok = true,
                Format = "pptx",
                FilePath = "C:\\fake\\exported.pptx",
                FileName = "exported.pptx",
                FileSizeBytes = 2048,
            });
        };

        var vm = new ChatViewModel(fakeApi);
        var artifact = new ArtifactItem
        {
            Type = "pptx",
            Title = "导出测试",
            RawContent = "# 导出内容测试",
        };

        vm.OpenArtifactCommand.Execute(artifact);

        // 切换主题为自然绿
        var greenTheme = vm.AvailableThemes.First(t => t.Id == "emerald_green");
        vm.SelectedTheme = greenTheme;

        await vm.ExportArtifactFileAsync("pptx");

        Assert.True(apiCalled);
        Assert.Contains("exported.pptx", vm.StatusMessage);
    }

    [Fact]
    public async Task ChatViewModel_InspectPpt_CallsApiAndSetsReport()
    {
        var fakeApi = new FakeDoc2kbApiService();
        var apiCalled = false;
        fakeApi.OnInspectCreativeArtifact = (content, ct) =>
        {
            apiCalled = true;
            return Task.FromResult(new PptInspectionReportDto
            {
                Score = 92,
                Grade = "S (卓越)",
                Summary = "结构严谨，节奏优良",
                SlideCount = 6,
                NotesCoveragePct = 100.0,
                ArchetypeDiversity = 4,
                Issues = new List<InspectionIssueDto>
                {
                    new() { Level = "info", Category = "视觉节奏", Message = "板式多样性良好" }
                }
            });
        };

        var vm = new ChatViewModel(fakeApi);
        var artifact = new ArtifactItem
        {
            Type = "pptx",
            Title = "自检测试",
            RawContent = "# 幻灯片内容",
        };

        vm.OpenArtifactCommand.Execute(artifact);
        await vm.InspectPptCommand.ExecuteAsync(null);

        Assert.True(apiCalled);
        Assert.True(vm.IsInspectionReportOpen);
        Assert.NotNull(vm.InspectionReport);
        Assert.Equal(92, vm.InspectionReport.Score);
        Assert.Equal("S (卓越)", vm.InspectionReport.Grade);
        Assert.Contains("92", vm.StatusMessage);
    }

    [Fact]
    public void ChatViewModel_SlideShowCommands_ToggleAndNavigate()
    {
        var fakeApi = new FakeDoc2kbApiService();
        var vm = new ChatViewModel(fakeApi);
        var artifact = new ArtifactItem
        {
            Type = "pptx",
            Title = "放映测试",
            RawContent = "# 第一页\n---\n# 第二页",
            Slides = new List<SlideItem>
            {
                new() { Index = 1, Title = "第一页" },
                new() { Index = 2, Title = "第二页" },
            }
        };

        vm.OpenArtifactCommand.Execute(artifact);
        Assert.False(vm.IsSlideShowOpen);

        // 开启大屏放映
        vm.OpenSlideShowCommand.Execute(null);
        Assert.True(vm.IsSlideShowOpen);
        Assert.True(vm.IsSpeakerNotesVisibleInSlideShow);

        // 切换提词小抄
        vm.ToggleSlideShowNotesCommand.Execute(null);
        Assert.False(vm.IsSpeakerNotesVisibleInSlideShow);

        // 翻页
        Assert.Equal(0, vm.CurrentSlideIndex);
        vm.NextSlideCommand.Execute(null);
        Assert.Equal(1, vm.CurrentSlideIndex);

        // 关闭放映
        vm.CloseSlideShowCommand.Execute(null);
        Assert.False(vm.IsSlideShowOpen);
    }

    [Fact]
    public async Task ChatViewModel_OpenWebPreview_CallsApiWithHtmlFormat()
    {
        var fakeApi = new FakeDoc2kbApiService();
        var apiCalled = false;
        fakeApi.OnExportCreativeArtifact = (req, ct) =>
        {
            apiCalled = true;
            Assert.Equal("html", req.Format);
            return Task.FromResult(new CreativeExportResponse
            {
                Ok = true,
                Format = "html",
                FilePath = "C:\\fake\\non_existent.html",
                FileName = "preview.html",
            });
        };

        var vm = new ChatViewModel(fakeApi);
        var artifact = new ArtifactItem
        {
            Type = "pptx",
            Title = "网页放映测试",
            RawContent = "# 网页放映内容",
        };

        vm.OpenArtifactCommand.Execute(artifact);
        await vm.OpenWebPreviewAsync();

        Assert.True(apiCalled);
    }
}
