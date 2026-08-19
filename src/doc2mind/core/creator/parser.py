"""从文本中解析 Artifact 结构与多板式幻灯片切片。

针对小参数模型/弱能力模型（0.5B ~ 7B / Ollama 量化模型）具备工业级超强容错自愈能力：
1. 自动闭合残缺或截断的 :::artifact 标签；
2. 无 :::artifact 标签时，根据内容特征智能识别 pptx / docx / xlsx；
3. 缺失 --- 分页符时，智能按连续 # 一级标题自动切分页；
4. 自动剔除口语废话（如“好的，为您生成如下PPT：”）；
5. 破损表格与缺失对齐线自动修复；
6. 弱模型常规要点自动晋升为视觉卡片。
"""

from __future__ import annotations

import re

from doc2mind.core.creator.models import (
    ArtifactModel,
    ArtifactType,
    MetricItem,
    SlideCardItem,
    SlideLayoutType,
    SlideModel,
    TimelineNodeItem,
)


def _clean_conversational_fluff(text: str) -> str:
    """剔除大模型在开头或结尾可能输出的口语废话。"""
    lines = text.splitlines()
    if not lines:
        return text

    # 常见开头废话模式
    fluff_prefixes = (
        r"^(好的|好的，|没问题|当然可以|这是为您|以下是为您|已为您|根据您的要求|为你生成|帮您制作).*",
        r"^(Here is|Sure|Certainly|Below is).*",
    )
    # 常见结尾废话模式
    fluff_suffixes = (
        r"^(希望以上|如果需要|以上内容|如有疑问|您可以|感谢您的使用).*",
        r"^(Hope this helps|Let me know|Feel free to).*",
    )

    start_idx = 0
    while start_idx < len(lines):
        line = lines[start_idx].strip()
        if not line:
            start_idx += 1
            continue
        if any(re.match(p, line, re.IGNORECASE) for p in fluff_prefixes) and not line.startswith(("#", ":::", "---")):
            start_idx += 1
        else:
            break

    end_idx = len(lines) - 1
    while end_idx >= start_idx:
        line = lines[end_idx].strip()
        if not line:
            end_idx -= 1
            continue
        if any(re.match(p, line, re.IGNORECASE) for p in fluff_suffixes) and not line.startswith(("#", ":::", "---")):
            end_idx -= 1
        else:
            break

    return "\n".join(lines[start_idx : end_idx + 1]).strip()


def _infer_artifact_type_from_content(content: str, default_type: str = "docx") -> tuple[ArtifactType, str]:
    """从纯 Markdown 文本特征中智能推断交付物类型（供小模型未写 :::artifact 时使用）。"""
    # 1. 如果包含 `---` 且有多个 `# `，大概率是 PPT
    dash_pages = re.split(r"(?m)^---\s*$", content)
    hash_headings = re.findall(r"(?m)^#\s+", content)
    if len(dash_pages) >= 2 or len(hash_headings) >= 3:
        # 若以幻灯片常见特征为主
        if any(kw in content for kw in ("<!-- note:", "<!-- layout:", "幻灯片", "Slide", "PPT", "演讲")):
            return ArtifactType.PPTX, "pptx"
        if len(dash_pages) >= 2 and len(hash_headings) >= 2:
            return ArtifactType.PPTX, "pptx"

    # 2. 如果包含大面积表格，推断为 Excel
    table_lines = [l for l in content.splitlines() if l.strip().startswith("|") and l.strip().endswith("|")]
    non_empty_lines = [l for l in content.splitlines() if l.strip()]
    if len(table_lines) >= 3 and len(table_lines) / max(1, len(non_empty_lines)) > 0.4:
        return ArtifactType.XLSX, "xlsx"

    # 3. 如果包含 html 标签
    if "<html" in content.lower() or "<div" in content.lower() or "<!doctype html" in content.lower():
        return ArtifactType.HTML, "html"

    # 默认
    for t in ArtifactType:
        if t.value == default_type:
            return t, default_type
    return ArtifactType.DOCX, "docx"


def extract_artifact(text: str, default_type: str = "docx") -> ArtifactModel:
    """从文本中提取 Artifact。具备小模型自愈容错能力。"""
    if not text:
        return ArtifactModel(
            artifact_type=ArtifactType(default_type),
            title="未命名交付物",
            raw_content="",
        )

    clean_text = _clean_conversational_fluff(text)

    # 1. 宽容度极高的 :::artifact 语法正则（支持空格、单双引号缺失、甚至末尾未闭合）
    pattern = r":::\s*artifact(?:\s+type=[\"']?([a-zA-Z0-9_-]+)[\"']?)?(?:\s+title=[\"']?([^\"'\n\r]+)[\"']?)?(?:\s+theme=[\"']?([a-zA-Z0-9_-]+)[\"']?)?\s*\n([\s\S]*?)(?::::|\Z)"
    match = re.search(pattern, clean_text, re.IGNORECASE)

    raw_content = clean_text
    title = "知识创作交付物"
    artifact_type_str = default_type
    theme = "tech_blue"

    if match:
        matched_type = match.group(1)
        matched_title = match.group(2)
        matched_theme = match.group(3)
        matched_content = match.group(4)

        if matched_type:
            artifact_type_str = matched_type.lower().strip()
        if matched_title:
            title = matched_title.strip()
        if matched_theme:
            theme = matched_theme.lower().strip()
        if matched_content:
            raw_content = matched_content.strip()
    else:
        # 小模型未输出 :::artifact 标签时的智能自愈推断
        atype_inferred, inferred_str = _infer_artifact_type_from_content(clean_text, default_type)
        artifact_type_str = inferred_str

        # 尝试从首个 # 一级标题提取标题
        for line in clean_text.splitlines():
            line_s = line.strip()
            if line_s.startswith("# "):
                title = line_s[2:].strip()
                break

    # 检查内容内部是否有 <!-- theme: xxx -->
    theme_match = re.search(r"<!--\s*theme:\s*([a-zA-Z0-9_-]+)\s*-->", raw_content, re.IGNORECASE)
    if theme_match:
        theme = theme_match.group(1).lower().strip()

    # 标准化 artifact_type
    atype = ArtifactType.DOCX
    for t in ArtifactType:
        if t.value == artifact_type_str:
            atype = t
            break

    artifact = ArtifactModel(
        artifact_type=atype,
        title=title,
        raw_content=raw_content,
        theme=theme,
    )

    # 若为 PPTX 格式，解析 Slide 切片
    if atype == ArtifactType.PPTX:
        artifact.slides = parse_pptx_slides(raw_content)

    return artifact


def parse_pptx_slides(content: str) -> list[SlideModel]:
    """将 Marp 或破损格式的 Markdown 内容解析为具有专业板式原型的 Slide 列表。"""
    if not content or not content.strip():
        return []

    # 1. 尝试按标准 `---` 分页
    raw_pages = re.split(r"(?m)^---\s*$", content)

    # 2. 小模型容错：如果全篇没有 `---` 分页符，但包含多个 `# ` 一级标题，则按 `# ` 智能切页
    if len(raw_pages) <= 1:
        # 按 `(?m)^(?=# )` 切分
        split_by_h1 = re.split(r"(?m)^(?=#\s+)", content)
        pages_candidate = [p.strip() for p in split_by_h1 if p.strip()]
        if len(pages_candidate) >= 2:
            raw_pages = pages_candidate

    slides: list[SlideModel] = []
    slide_idx = 1
    for page in raw_pages:
        page_clean = page.strip()
        if not page_clean:
            continue

        slide = _parse_single_slide(page_clean, slide_idx)
        slides.append(slide)
        slide_idx += 1

    return slides


def _parse_single_slide(page_text: str, index: int) -> SlideModel:
    """解析单页幻灯片内容并进行深度板式嗅探与容错。"""
    speaker_notes = ""
    explicit_layout: SlideLayoutType | None = None

    # 1. 提取演讲备注 <!-- note: ... -->
    note_match = re.search(r"<!--\s*note:\s*([\s\S]*?)-->", page_text, re.IGNORECASE)
    if note_match:
        speaker_notes = note_match.group(1).strip()
        page_text = page_text[: note_match.start()] + page_text[note_match.end() :]
        page_text = page_text.strip()

    # 2. 提取显式板式声明 <!-- layout: cards|metrics|timeline|table|quote|agenda|cover -->
    layout_match = re.search(r"<!--\s*layout:\s*([a-zA-Z0-9_-]+)\s*-->", page_text, re.IGNORECASE)
    if layout_match:
        val = layout_match.group(1).lower().strip()
        try:
            explicit_layout = SlideLayoutType(val)
        except ValueError:
            explicit_layout = None
        page_text = page_text[: layout_match.start()] + page_text[layout_match.end() :]
        page_text = page_text.strip()

    title = f"第 {index} 页"
    subtitle = ""
    bullet_points: list[str] = []
    table_lines: list[str] = []
    cards: list[SlideCardItem] = []
    metrics: list[MetricItem] = []
    timeline_nodes: list[TimelineNodeItem] = []
    quote_text = ""
    quote_author = ""
    is_cover = index == 1

    lines = page_text.splitlines()
    current_card: SlideCardItem | None = None

    for line in lines:
        ls = line.strip()
        if not ls:
            continue

        # 标题识别（支持 # 标题、第X页：标题、Slide X: 标题）
        if (ls.startswith("# ") or re.match(r"^(?:第[0-9一二三四五六七八九十]+页|Slide\s*\d+)[:：]\s*", ls)) and title == f"第 {index} 页":
            if ls.startswith("# "):
                title = ls[2:].strip()
            else:
                title = re.sub(r"^(?:第[0-9一二三四五六七八九十]+页|Slide\s*\d+)[:：]\s*", "", ls).strip()
            continue

        # 封面副标题
        if ls.startswith("## ") and is_cover and not subtitle:
            subtitle = ls[3:].strip()
            continue

        # 卡片分割 ### Card Title 或 **模块名**
        if ls.startswith("### "):
            if current_card:
                cards.append(current_card)
            c_title = ls[4:].strip()
            current_card = SlideCardItem(title=c_title)
            continue

        # 引用块 > quote
        if ls.startswith(">"):
            q_clean = ls.lstrip(">").strip()
            if q_clean:
                if not quote_text:
                    quote_text = q_clean
                else:
                    quote_text += "\n" + q_clean
            continue

        # 表格行（支持容错：两端或中间带竖线的行）
        if "|" in ls and ls.count("|") >= 2:
            table_lines.append(ls)
            continue

        # 列表项（支持 -, *, +, •, 以及小模型爱用的 emoji 列表 🔹, 📌, 1.）
        if ls.startswith(("- ", "* ", "+ ", "• ", "🔹 ", "📌 ", "▪ ")):
            item_text = re.sub(r"^[-*+•🔹📌▪]\s*", "", ls).strip()
            if current_card is not None:
                current_card.bullets.append(item_text)
            else:
                bullet_points.append(item_text)
            continue

        # 有序列表项 1. 2. 3.
        if re.match(r"^\d+[\.、\)]\s*", ls):
            item_text = re.sub(r"^\d+[\.、\)]\s*", "", ls).strip()
            if current_card is not None:
                current_card.bullets.append(item_text)
            else:
                bullet_points.append(item_text)
            continue

        # 普通段落（小模型经常直接输出段落文字）
        if not ls.startswith("#") and not ls.startswith("<!--"):
            if current_card is not None:
                if not current_card.content:
                    current_card.content = ls
                else:
                    current_card.bullets.append(ls)
            else:
                if len(ls) < 140:
                    bullet_points.append(ls)

    if current_card:
        cards.append(current_card)

    # 表格容错解析（自动过滤对齐分隔线，自动补齐列）
    table_data = None
    if len(table_lines) >= 2:
        table_data = []
        for t_line in table_lines:
            if re.match(r"^\|?[\s\-:|]+\|?$", t_line):
                continue
            cols = [c.strip() for c in t_line.strip("|").split("|")]
            if any(cols):
                table_data.append(cols)

    # 3. 启发式大数字 KPI 提取 (如 "99.8% 可用性", "10x 性能提升", "35ms 低延迟")
    metric_regex = r"^([0-9]+(?:\.[0-9]+)?(?:%|x|X|ms|s|MB|GB|KB|倍|万|亿)?)\s*[:：\-—]\s*(.*)$"
    for b in bullet_points:
        m_match = re.match(metric_regex, b)
        if m_match:
            val = m_match.group(1).strip()
            desc = m_match.group(2).strip()
            metrics.append(MetricItem(value=val, label=desc))

    # 4. 启发式时间线/阶段路线图提取 (如 "阶段一: 需求分析", "Step 1: 架构设计")
    timeline_regex = r"^(阶段[一二三四五六七八九十1-9]|Step\s*\d+|Q[1-4]|步骤[1-9]|第[一二三四五]步)\s*[:：\-—]\s*(.*)$"
    for b in bullet_points:
        t_match = re.match(timeline_regex, b, re.IGNORECASE)
        if t_match:
            stage_name = t_match.group(1).strip()
            stage_desc = t_match.group(2).strip()
            timeline_nodes.append(TimelineNodeItem(stage=stage_name, title=stage_desc))

    # 封面精准判定：第一页且无要点/卡片/表格/指标，或者存在副标题且无其他复杂内容
    has_body_content = bool(bullet_points or cards or table_data or metrics or timeline_nodes or quote_text)
    is_real_cover = (index == 1) and (not has_body_content or (bool(subtitle) and len(bullet_points) <= 1))

    # 5. 智能板式裁决 (Inference)
    layout = explicit_layout or SlideLayoutType.GENERAL
    if not explicit_layout:
        if is_real_cover:
            layout = SlideLayoutType.COVER
        elif any(k in title for k in ("目录", "议程", "Agenda", "大纲")) and len(bullet_points) >= 2:
            layout = SlideLayoutType.AGENDA
        elif table_data and len(table_data) >= 2:
            layout = SlideLayoutType.TABLE
        elif len(metrics) >= 2 and len(metrics) == len(bullet_points):
            layout = SlideLayoutType.METRICS
        elif len(timeline_nodes) >= 2 and len(timeline_nodes) == len(bullet_points):
            layout = SlideLayoutType.TIMELINE
        elif len(cards) in (2, 3, 4):
            layout = SlideLayoutType.CARDS
        elif quote_text:
            layout = SlideLayoutType.QUOTE
        elif len(bullet_points) in (2, 3, 4) and all(len(b) < 80 for b in bullet_points):
            # 针对弱模型：输出 2~4 个精简要点时，自动晋升为卡片网格，视觉效果大幅提升
            layout = SlideLayoutType.CARDS
            cards = [SlideCardItem(title=f"核心要点 0{i+1}", content=b) for i, b in enumerate(bullet_points)]

    return SlideModel(
        index=index,
        title=title,
        subtitle=subtitle,
        layout=layout,
        bullet_points=bullet_points,
        speaker_notes=speaker_notes,
        table_data=table_data,
        is_cover=is_cover,
        cards=cards,
        metrics=metrics,
        timeline_nodes=timeline_nodes,
        quote_text=quote_text,
        quote_author=quote_author,
    )
