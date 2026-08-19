"""PPT 效果自检与质量诊断系统单元测试。"""

from __future__ import annotations

from doc2mind.core.creator import (
    PptInspectionReport,
    inspect_presentation,
)


def test_inspect_high_quality_presentation():
    """测试高质量完整演示文稿：应获得 S 或 A+ 极高健康度得分。"""
    perfect_ppt = """
:::artifact type="pptx" title="企业级架构汇报" theme="tech_blue"
---
# DocMind 智能创作中枢
## 离线优先架构设计方案
<!-- note: 各位领导好，今天向大家汇报架构设计。 -->
---
# 汇报议程与结构
- 1. 核心架构设计
- 2. 关键性能指标
- 3. 落地推进路线图
<!-- note: 这是本次汇报的整体目录。 -->
---
<!-- layout: cards -->
# 核心架构三大支柱
### 向量引擎
- CPU 轻量 ONNX
- 35MB 极低内存
### 存储与图谱
- SQLite 原生扩展
- 秒级拓扑关系查询
### 物理排版引擎
- 原生几何渲染
- 8 大专业板式
<!-- note: 这是系统最核心的三大技术基石。 -->
---
<!-- layout: metrics -->
# 关键性能指标
- 99.9% : 可用性与稳定性
- 10x : 检索吞吐加速
- 12ms : 平均端到端延迟
- 0MB : 独立 GPU 显存依赖
<!-- note: 实测性能指标非常亮眼。 -->
---
<!-- layout: timeline -->
# 演进路线图
- 阶段一 : 架构瘦身与 ONNX 改造
- 阶段二 : 8 大板式渲染引擎研发
- 阶段三 : 全自动 AI 整理与商业交付
<!-- note: 目前已圆满完成阶段一与阶段二。 -->
:::
"""
    report = inspect_presentation(perfect_ppt)
    assert isinstance(report, PptInspectionReport)
    assert report.score >= 85
    assert "卓越" in report.grade or "优秀" in report.grade
    assert report.slide_count == 5
    assert report.notes_coverage_pct == 100.0
    assert report.archetype_diversity >= 3
    assert len(report.highlights) >= 2


def test_inspect_flawed_presentation():
    """测试存在缺陷的演示文稿：缺少封面、缺少目录、单页文字过密。"""
    flawed_ppt = """
:::artifact type="pptx" title="问题演示文稿"
---
# 这一页不是封面而且没有副标题
- 缺少副标题
- 篇幅很短
---
# 第二页文字极其拥挤的文字墙
- 这是一段非常冗长而且繁杂的文字内容，我们一直在罗列各种细节，并没有进行提炼，也没有拆分为多个卡片，导致观众在阅读的时候感到极度拥挤和疲惫。这里继续补充更多的文字描述，试图把所有的背景、所有的实现过程和所有琐碎的数据全部堆叠在单张幻灯片里面。观众根本无法在 5 秒钟内抓住核心结论，这样的幻灯片在商业汇报中是非常不合格的，存在严重的视觉疲劳问题。
- 这里还有第二个超长的要点，继续充斥着大量的技术细节和背景交代，完全没有进行要点提炼和视觉化拆分，导致整个页面密密麻麻全是文字，严重违反了演示文稿的基本排版准则！
"""
    report = inspect_presentation(flawed_ppt)
    assert report.score < 80
    assert report.notes_coverage_pct == 0.0

    # 验证是否正确检测出问题分类
    categories = [i.category for i in report.issues]
    assert "文字密度" in categories
    assert "演讲配套" in categories

    # 验证是否有具体的修复建议
    wall_of_text_issue = next(i for i in report.issues if i.category == "文字密度")
    assert "文字墙" in wall_of_text_issue.message
    assert len(wall_of_text_issue.fix_suggestion) > 0


def test_inspect_empty_presentation():
    """测试空内容容错。"""
    report = inspect_presentation("")
    assert report.score == 0
    assert report.grade == "C"
    assert len(report.issues) > 0
