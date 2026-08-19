"""企业级 PPT 效果自检与质量诊断引擎 (Slide Quality Inspector & Linter)。

提供 5 大维度全方位健康度体检：
1. 结构完整度 (Structure): 封面、目录、适度篇幅；
2. 文字密度与易读性 (Density & Readability): 墙式文字预警、过简空洞提示；
3. 视觉节奏与板式丰富度 (Visual Rhythm & Diversity): 卡片/看板/时间线/表格多样性；
4. 演讲配套完备度 (Presentation Readiness): 提词器演讲备注覆盖率；
5. 数据与论据支撑度 (Data & Evidence): 大数字 KPI 与表格支撑。
"""

from __future__ import annotations

import re

from doc2mind.core.creator.models import (
    ArtifactModel,
    InspectionIssue,
    InspectionLevel,
    PptInspectionReport,
    SlideLayoutType,
)
from doc2mind.core.creator.parser import extract_artifact


def inspect_presentation(artifact_or_text: ArtifactModel | str) -> PptInspectionReport:
    """对 PPT 演示文稿进行全方位效果自检，输出 0-100 健康度体检报告与优化建议。"""
    if isinstance(artifact_or_text, str):
        artifact = extract_artifact(artifact_or_text, default_type="pptx")
    else:
        artifact = artifact_or_text

    slides = artifact.slides
    total_slides = len(slides)

    issues: list[InspectionIssue] = []
    highlights: list[InspectionIssue] = []
    recommendations: list[str] = []

    if total_slides == 0:
        return PptInspectionReport(
            score=0,
            grade="C",
            summary="未检测到有效幻灯片页面，内容为空或缺少必要分页。",
            issues=[
                InspectionIssue(
                    level=InspectionLevel.ERROR,
                    category="结构完整度",
                    message="幻灯片总页数为 0，无法生成有效演示文稿。",
                    fix_suggestion="请在每页幻灯片之间添加 `---` 分页符并提供标题与要点内容。",
                )
            ],
            recommendations=["添加第一页封面 `# 主标题` 与 `## 副标题`", "添加后续内容页并用 `---` 分页"],
        )

    # 1. 基础指标统计
    total_words = 0
    slides_with_notes = 0
    layouts_used = set()
    cards_count = 0
    metrics_count = 0
    timelines_count = 0
    tables_count = 0
    quotes_count = 0

    slide_word_counts: list[int] = []

    for s in slides:
        layouts_used.add(s.layout)
        # 统计单页有效文字（标题 + 副标题 + 要点 + 卡片 + 表格 + 金句）
        slide_text_chunks = [s.title, s.subtitle]
        slide_text_chunks.extend(s.bullet_points)
        for c in s.cards:
            slide_text_chunks.append(c.title)
            slide_text_chunks.append(c.content)
            slide_text_chunks.extend(c.bullets)
        for m in s.metrics:
            slide_text_chunks.append(m.value)
            slide_text_chunks.append(m.label)
        for t in s.timeline_nodes:
            slide_text_chunks.append(t.stage)
            slide_text_chunks.append(t.title)
        if s.quote_text:
            slide_text_chunks.append(s.quote_text)
        if s.table_data:
            for row in s.table_data:
                slide_text_chunks.extend(row)

        full_slide_str = "".join(slide_text_chunks)
        # 过滤空白后的汉字与单词总数
        w_count = len(re.findall(r"[\u4e00-\u9fa5]|[a-zA-Z0-9]+", full_slide_str))
        slide_word_counts.append(w_count)
        total_words += w_count

        if s.speaker_notes.strip():
            slides_with_notes += 1

        if s.cards:
            cards_count += 1
        if s.metrics:
            metrics_count += 1
        if s.timeline_nodes:
            timelines_count += 1
        if s.table_data:
            tables_count += 1
        if s.quote_text:
            quotes_count += 1

    avg_words = round(total_words / max(1, total_slides), 1)
    notes_coverage_pct = round((slides_with_notes / total_slides) * 100, 1)
    archetype_diversity = len(layouts_used)

    # 2. 扣分制与评分体系（起始分 100 分）
    score = 100

    # ---------------- 维度一：结构完整度 ----------------
    has_cover = slides[0].layout == SlideLayoutType.COVER or slides[0].is_cover
    if not has_cover:
        score -= 8
        issues.append(
            InspectionIssue(
                level=InspectionLevel.WARNING,
                category="结构完整度",
                message="第 1 页缺少明确的封面属性（建议包含主标题与副标题/机构说明）。",
                slide_index=1,
                fix_suggestion="在第 1 页添加 `## 副标题 / 汇报人` 明确汇报主题与演讲者身份。",
            )
        )
    else:
        highlights.append(
            InspectionIssue(
                level=InspectionLevel.INFO,
                category="结构完整度",
                message="包含规范的封面幻灯片，主题鲜明。",
            )
        )

    has_agenda = any(s.layout == SlideLayoutType.AGENDA or any(k in s.title for k in ("目录", "议程", "Agenda")) for s in slides[:3])
    if total_slides >= 5 and not has_agenda:
        score -= 5
        issues.append(
            InspectionIssue(
                level=InspectionLevel.SUGGESTION,
                category="结构完整度",
                message=f"幻灯片篇幅较长（{total_slides} 页），但前 3 页未检测到「目录/议程」导引页。",
                fix_suggestion="建议在第 2 页添加 `# 目录与议程` 概括 3~5 个核心章节，帮助听众建立宏观框架。",
            )
        )

    if total_slides < 3:
        score -= 10
        issues.append(
            InspectionIssue(
                level=InspectionLevel.WARNING,
                category="结构完整度",
                message=f"演示文稿页数偏少（仅 {total_slides} 页），难以形成完整的叙事闭环。",
                fix_suggestion="建议扩充至 4~10 页，涵盖背景、方案、数据论据与总结。",
            )
        )
    elif total_slides > 25:
        score -= 5
        issues.append(
            InspectionIssue(
                level=InspectionLevel.SUGGESTION,
                category="结构完整度",
                message=f"幻灯片页数较多（{total_slides} 页），常规汇报容易超时。",
                fix_suggestion="建议提炼核心论点，将非核心细节放入附录。",
            )
        )

    # ---------------- 维度二：文字密度与易读性 ----------------
    for idx, (s, w_count) in enumerate(zip(slides, slide_word_counts, strict=False), 1):
        # 墙式文字警告 (Wall of Text)
        if w_count > 200 and s.layout != SlideLayoutType.TABLE:
            score -= 10
            issues.append(
                InspectionIssue(
                    level=InspectionLevel.WARNING,
                    category="文字密度",
                    message=f"第 {idx} 页文字量过大（{w_count} 字），存在“文字墙”现象，观众阅读压力大。",
                    slide_index=idx,
                    fix_suggestion="建议将长段落拆解为 2~3 个短句要点，或提炼为 `### 子卡片` 网格排版。",
                )
            )
        # 单页空洞提示
        elif w_count < 15 and s.layout != SlideLayoutType.COVER and s.layout != SlideLayoutType.QUOTE:
            score -= 5
            issues.append(
                InspectionIssue(
                    level=InspectionLevel.SUGGESTION,
                    category="文字密度",
                    message=f"第 {idx} 页内容过于简短（仅 {w_count} 字），信息量可能不足。",
                    slide_index=idx,
                    fix_suggestion="建议补充 1~2 条具体支撑要点或数据佐证。",
                )
            )
        # 要点过多警告
        if len(s.bullet_points) > 6:
            score -= 6
            issues.append(
                InspectionIssue(
                    level=InspectionLevel.WARNING,
                    category="文字密度",
                    message=f"第 {idx} 页要点过多（{len(s.bullet_points)} 条），超出人脑短期记忆最佳负荷（3~6 条）。",
                    slide_index=idx,
                    fix_suggestion="建议删减次要要点，或按模块归纳为 2~3 个 `###` 卡片。",
                )
            )

    # ---------------- 维度三：视觉节奏与板式丰富度 ----------------
    if total_slides >= 3:
        if archetype_diversity == 1:
            score -= 12
            issues.append(
                InspectionIssue(
                    level=InspectionLevel.WARNING,
                    category="视觉节奏",
                    message="全篇仅使用单一种类板式，视觉呈现单调，缺乏视觉节奏感与高潮。",
                    fix_suggestion="尝试在关键成果页使用 `<!-- layout: metrics -->`（大数字），在架构页使用 `<!-- layout: cards -->`（卡片网格）。",
                )
            )
        elif archetype_diversity >= 3:
            highlights.append(
                InspectionIssue(
                    level=InspectionLevel.INFO,
                    category="视觉节奏",
                    message=f"板式丰富度高（涵盖 {archetype_diversity} 种专业板式原型），视觉节奏感良好！",
                )
            )

    # ---------------- 维度四：演讲配套完备度 ----------------
    if notes_coverage_pct < 40 and total_slides >= 2:
        score -= 10
        issues.append(
            InspectionIssue(
                level=InspectionLevel.SUGGESTION,
                category="演讲配套",
                message=f"演讲备注（Speaker Notes）覆盖率偏低（仅 {notes_coverage_pct}%），现场汇报可能缺乏提词支撑。",
                fix_suggestion="在幻灯片末尾添加 `<!-- note: 本页演讲口播词与要点补充 -->` 为演讲者提供提词辅助。",
            )
        )
    elif notes_coverage_pct >= 75:
        highlights.append(
            InspectionIssue(
                level=InspectionLevel.INFO,
                category="演讲配套",
                message=f"演讲备注覆盖率达 {notes_coverage_pct}%，现场提词完备，随时可开启汇报！",
            )
        )

    # ---------------- 维度五：数据与论据支撑度 ----------------
    has_data_support = (metrics_count > 0) or (tables_count > 0) or (cards_count > 0)
    if not has_data_support and total_slides >= 4:
        score -= 6
        issues.append(
            InspectionIssue(
                level=InspectionLevel.SUGGESTION,
                category="论据支撑",
                message="全篇未发现大数字 KPI 看板或对比表格，量化论据相对薄弱。",
                fix_suggestion="建议使用 `<!-- layout: metrics -->`（如 `99.8% : 可用性`）或 Markdown 表格增强量化说服力。",
            )
        )

    # 最终分数修正 (0-100)
    score = max(0, min(100, score))

    # 等级评定
    if score >= 95:
        grade = "S (卓越)"
        summary = "🎉 完美级演示文稿！结构严谨、板式丰富、节奏分明且演讲配套齐备，可直接用于高规格汇报！"
    elif score >= 85:
        grade = "A+ (优秀)"
        summary = "✨ 优秀商业级演示文稿！视觉节奏与内容完整度俱佳，仅有少量微调空间。"
    elif score >= 70:
        grade = "A (良好)"
        summary = "👍 良好演示文稿。核心逻辑清晰，建议根据诊断提示微调文字密度或丰富板式。"
    elif score >= 55:
        grade = "B (需优化)"
        summary = "⚠️ 基础可用，但存在文字过密、板式单一或缺少结构导引等问题，建议优化。"
    else:
        grade = "C (预警)"
        summary = "🚨 存在较多结构性或可读性缺陷，强烈建议按优化建议重构。"

    # 归纳推荐行动
    if not has_cover:
        recommendations.append("补充规范的封面幻灯片与副标题")
    if total_slides >= 5 and not has_agenda:
        recommendations.append("在第 2 页增加目录导航（Agenda）")
    if any(i.level == InspectionLevel.WARNING and i.category == "文字密度" for i in issues):
        recommendations.append("精简文字密度，拆分单页超过 200 字的“文字墙”")
    if archetype_diversity < 3 and total_slides >= 4:
        recommendations.append("引入大数字 KPI 看板、多列卡片或横向时间线以提升视觉节奏")
    if notes_coverage_pct < 50:
        recommendations.append("补充 Slide Notes 演讲备注，提升现场口播提词完备度")

    if not recommendations:
        recommendations.append("文稿质量极佳，可直接导出并在各类场景展示汇报！")

    highlight_msgs = [h.message for h in highlights]

    return PptInspectionReport(
        score=score,
        grade=grade,
        summary=summary,
        slide_count=total_slides,
        notes_coverage_pct=notes_coverage_pct,
        archetype_diversity=archetype_diversity,
        total_words=total_words,
        avg_words_per_slide=avg_words,
        issues=issues,
        recommendations=recommendations,
        highlights=highlight_msgs,
    )
