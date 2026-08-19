"""测试对小参数模型/弱能力模型的自愈容错与自适应增强。"""

from __future__ import annotations

from doc2mind.core.creator import (
    SlideLayoutType,
    extract_artifact,
)


def test_unclosed_artifact_tag_self_healing():
    """小模型生成到一半被截断，漏写结尾的 :::"""
    truncated_output = """好的，为您生成如下演示文稿：
:::artifact type="pptx" title="小模型截断测试" theme="emerald_green"
---
# 封面标题
## 离线优先
---
# 核心亮点
- 亮点一：极低显存
- 亮点二：毫秒级响应
"""
    artifact = extract_artifact(truncated_output)
    assert artifact.artifact_type.value == "pptx"
    assert artifact.title == "小模型截断测试"
    assert artifact.theme == "emerald_green"
    assert len(artifact.slides) == 2
    assert artifact.slides[0].title == "封面标题"


def test_missing_artifact_tag_auto_inference():
    """小模型完全没写 :::artifact 标签，只输出了纯 Markdown"""
    plain_markdown = """
# 智能知识库平台
## 个人与团队离线助理
---
# 核心技术架构
- 向量检索
- 图谱拓扑
---
# 性能指标
- 99.8% : 可用性
- 10x : 加速比
"""
    artifact = extract_artifact(plain_markdown, default_type="docx")
    # 智能推断为 pptx
    assert artifact.artifact_type.value == "pptx"
    assert artifact.title == "智能知识库平台"
    assert len(artifact.slides) == 3


def test_missing_dash_pages_splits_by_h1():
    """小模型漏写 --- 分页符，但输出了多个 # 一级标题"""
    no_dashes = """
:::artifact type="pptx" title="无分隔线测试"
# 封面页
## 方案副标题
# 第一章：背景分析
- 行业现状
- 客户痛点
# 第二章：解决方案
- 架构设计
- 落地路径
:::
"""
    artifact = extract_artifact(no_dashes)
    assert artifact.artifact_type.value == "pptx"
    assert len(artifact.slides) == 3
    assert artifact.slides[0].title == "封面页"
    assert artifact.slides[1].title == "第一章：背景分析"
    assert artifact.slides[2].title == "第二章：解决方案"


def test_conversational_fluff_stripping():
    """自动剔除开头与结尾的口语废话"""
    fluffy_text = """
好的，已为您精心制作了如下 PPT 大纲，请查收：

:::artifact type="pptx" title="清洗测试"
---
# 核心结论
- 要点一
- 要点二
:::

希望以上内容对您有所帮助，如果有任何需要修改的地方请随时告诉我！
"""
    artifact = extract_artifact(fluffy_text)
    assert artifact.title == "清洗测试"
    assert len(artifact.slides) == 1
    assert "好的，已为您精心制作" not in artifact.raw_content
    assert "希望以上内容对您有所帮助" not in artifact.raw_content


def test_weak_model_bullets_promoted_to_cards():
    """弱模型输出的 3 条简易要点，自动晋升为卡片网格板式"""
    simple_bullets = """
:::artifact type="pptx" title="卡片晋升测试"
---
# 三大核心支撑
- 纯本地运行保障隐私安全
- ONNX 引擎大幅降低内存开销
- 原生几何渲染输出高保真 PPT
:::
"""
    artifact = extract_artifact(simple_bullets)
    assert len(artifact.slides) == 1
    slide = artifact.slides[0]
    # 自动裁决为 CARDS 并且生成 3 个卡片对象
    assert slide.layout == SlideLayoutType.CARDS
    assert len(slide.cards) == 3
    assert slide.cards[0].title == "核心要点 01"
    assert "纯本地运行保障隐私安全" in slide.cards[0].content
