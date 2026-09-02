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

    [Fact]
    public void ChatMessage_PptSlides_MixedLayouts_KeepAllContent()
    {
        var msg = new ChatMessage { Role = "assistant" };

        var text = @"
:::artifact type=""pptx"" title=""混合板式回归防护""
---
# 第一页无副标题
- 项目背景说明
- 目标与范围
---
# 混合卡片与要点
- 总体要点一
- 总体要点二
### 模块C
- 模块细节
---
# 纯指标页
- 99.9% : 服务可用性
- 10x : 吞吐提升
---
# 指标加普通要点
- 99.9% : 准确率
- 架构重构说明
---
<!-- layout: table -->
# 方案对比
| 方案 | 性能 | 成本 |
| --- | --- | --- |
| A | 高 | 低 |
| B | 中 | 中 |
:::
";
        msg.AppendToken(text);
        msg.ForceRefreshRender();

        Assert.NotNull(msg.Artifact);
        Assert.Equal(5, msg.Artifact.SlideCount);

        // 页 1：无副标题的首页不该被判成封面，否则正文要点会被封面框架吞掉
        var p1 = msg.Artifact.Slides[0];
        Assert.False(p1.IsCover);
        Assert.True(p1.HasBullets);
        Assert.Equal(2, p1.BulletPoints.Count);

        // 页 2：卡片与普通要点叠加共存，普通要点不得因命中卡片板式而丢失
        var p2 = msg.Artifact.Slides[1];
        Assert.Single(p2.Cards);
        Assert.Equal(2, p2.BulletPoints.Count);
        Assert.True(p2.HasBullets);

        // 页 3：纯指标页应吸收对应要点，避免 KPI 卡片与要点文本重复渲染
        var p3 = msg.Artifact.Slides[2];
        Assert.Equal(2, p3.Metrics.Count);
        Assert.Empty(p3.BulletPoints);
        Assert.True(p3.IsMetrics);

        // 页 4：指标 + 普通要点混合，两者都必须保留
        var p4 = msg.Artifact.Slides[3];
        Assert.Single(p4.Metrics);
        Assert.Contains("架构重构说明", p4.BulletPoints);
        Assert.Equal("general", p4.Layout);

        // 页 5：表格必须能渲染出来（此前卡片预览缺少表格区域，整页内容为空）
        var p5 = msg.Artifact.Slides[4];
        Assert.True(p5.HasTable);
        Assert.Equal(3, p5.TableLines.Count);
        Assert.Contains("方案", p5.TableLines[0]);
    }

    [Fact]
    public void ChatMessage_InferPptArtifact_WhenModelOmitsArtifactWrapper()
    {
        var msg = new ChatMessage { Role = "assistant" };

        // 模型未输出 :::artifact 包裹，只给了一份纯 Markdown 的 PPT 设计方案
        var text = @"
## 动平衡原理 PPT 设计方案

> 目标：为技术培训准备一份演示文稿，内容引用本地知识库[1]。

---

### 目录（Slide 1）

| 页码 | 标题 |
|------|------|
| 1 | 标题页 |
| 2 | 目录 |

---

### Slide 3 – 动平衡概述

- 定义：通过校正不平衡使转子高速旋转时保持平稳
- 目标：降低振动、延长寿命

> 讲稿提示：先提一句动平衡是核心质量控制环节。

---

### Slide 5 – 双面系统建模

- 采集双面传感器振动矢量
- 计算影响系数矩阵并求解校正量

---

## 关键洞察

- 刚度匹配可显著提升求解精度
";
        msg.AppendToken(text);
        msg.ForceRefreshRender();

        Assert.True(msg.HasArtifact);
        Assert.NotNull(msg.Artifact);
        Assert.True(msg.Artifact!.IsPpt);
        Assert.True(msg.Artifact.SlideCount >= 2);
        Assert.Contains("动平衡", msg.Artifact.Title);
    }

    [Fact]
    public void ChatMessage_DoesNotInferArtifact_ForOrdinaryAnswer()
    {
        var msg = new ChatMessage { Role = "assistant" };

        // 普通短回答（含分隔线与表格）不应被误判成创作物
        var text = "动平衡是旋转机械的关键环节。\n\n---\n\n| 项目 | 说明 |\n|------|------|\n| 振动 | 降低 |";
        msg.AppendToken(text);
        msg.ForceRefreshRender();

        Assert.False(msg.HasArtifact);
    }

    [Fact]
    public void ChatMessage_PptSlide_MixedArchetypesOnOnePage_SinglePrimaryVisualNoLoss()
    {
        var msg = new ChatMessage { Role = "assistant" };

        // 模型自由输出：单页混合 页级指标 + 卡片（无 quote/时间线干扰）
        var text = @"
:::artifact type=""pptx"" title=""混合页回归""
---
# 单页混合视觉
- 99.9% : 服务可用性
- 10x : 检索吞吐提升
### 模块A
- 细节一
### 模块B
- 细节二
:::
";
        msg.AppendToken(text);
        msg.ForceRefreshRender();

        Assert.NotNull(msg.Artifact);
        var page = msg.Artifact!.Slides[0];

        // 主视觉唯一：卡片页渲染卡片区，指标不得作为独立大块同时显示（否则堆叠混乱）
        Assert.Equal("cards", page.Layout);
        Assert.True(page.ShowCards);
        Assert.Equal(2, page.Cards.Count);
        Assert.False(page.ShowMetrics);
        Assert.False(page.ShowTimeline);
        Assert.False(page.ShowQuote);

        // 指标数据不丢失，降级为补充要点显示
        Assert.Contains(page.BulletPoints, b => b.Contains("99.9%"));
        Assert.Contains(page.BulletPoints, b => b.Contains("10x"));
    }

    [Fact]
    public void ChatMessage_DoesNotInferArtifact_ForLongTextWithoutSlideFeatures()
    {
        var msg = new ChatMessage { Role = "assistant" };

        // 长文本 + 多个 --- 分隔，但没有任何幻灯片特征，不应被误判成 PPT
        var text = string.Join("\n---\n",
            Enumerable.Repeat("这是一段普通的说明性文字，用于验证长文本不会因为篇幅或分隔线就被误判成创作交付物。", 6));
        msg.AppendToken(text);
        msg.ForceRefreshRender();

        Assert.False(msg.HasArtifact);
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
