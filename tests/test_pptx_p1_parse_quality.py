"""P1 解析质量回归：## 降级、长段落拆分、单元格清洗、多表切分。"""

from __future__ import annotations

from doc2mind.core.creator import (
    PARAGRAPH_MAX_LEN,
    clean_markdown_inline,
    extract_artifact,
    parse_pptx_slides,
    split_long_paragraph,
)


def test_h2_non_cover_not_dropped():
    md = """
:::artifact type="pptx" title="二级标题保留"
---
# 封面主标题
## 封面副标题
---
## 非封面章节名
- 要点一
- 要点二
---
# 正式页
## 会被降级为要点的二级标题
- 其他要点
:::
"""
    artifact = extract_artifact(md, default_type="pptx")
    assert len(artifact.slides) == 3
    # 第 2 页无 # 标题 → ## 成为弱标题
    s2 = artifact.slides[1]
    assert s2.title == "非封面章节名"
    assert "要点一" in s2.bullet_points
    # 第 3 页已有 # 标题 → ## 降级为 bullet，禁止丢弃
    s3 = artifact.slides[2]
    assert s3.title == "正式页"
    assert any("会被降级为要点" in b for b in s3.bullet_points)


def test_long_paragraph_split_not_dropped():
    long_para = "挠度是梁在荷载作用下产生的竖向位移。" * 12  # 约 200+ 字
    assert len(long_para) > PARAGRAPH_MAX_LEN
    pieces = split_long_paragraph(long_para)
    assert len(pieces) >= 2
    assert all(len(p) <= PARAGRAPH_MAX_LEN for p in pieces)
    # 内容完整：去掉句读后拼回
    assert "".join(pieces).replace("。", "") == long_para.replace("。", "")

    md = f"""
:::artifact type="pptx" title="长段落"
---
# 封面页
## 副标题
---
# 长段落页
{long_para}
:::
"""
    artifact = extract_artifact(md, default_type="pptx")
    body = " ".join(artifact.slides[1].bullet_points)
    assert "挠度" in body
    # 不再静默丢弃：整段关键词应出现在 bullets 中
    assert len(artifact.slides[1].bullet_points) >= 2


def test_clean_markdown_inline_math_and_bold():
    assert clean_markdown_inline("**四次方正比**") == "四次方正比"
    assert clean_markdown_inline("$q$") == "q"
    assert clean_markdown_inline("$\\delta$") == "δ"
    assert clean_markdown_inline("$L/250$") == "L/250"
    assert clean_markdown_inline("$E I$") == "E I"
    assert "≤" in clean_markdown_inline("$\\le$")


def test_table_cells_cleaned():
    md = """
:::artifact type="pptx" title="表格清洗"
---
# 封面页
## 副标题
---
# 指标表
| 符号 | 含义 | 限值 |
| --- | --- | --- |
| $q$ | **均布荷载** | — |
| $\\delta$ | 挠度 | $L/250$ |
:::
"""
    artifact = extract_artifact(md, default_type="pptx")
    table = artifact.slides[1].table_data
    assert table is not None
    flat = [c for row in table for c in row]
    assert not any("**" in c for c in flat)
    assert not any("$" in c for c in flat)
    assert "q" in flat
    assert "δ" in flat
    assert any("L/250" in c for c in flat)


def test_multi_table_split_to_extra_slides():
    md = """
:::artifact type="pptx" title="多表切分"
---
# 数据对比
| 维度 | A | B |
| --- | --- | --- |
| 造价 | 高 | 低 |
| 工期 | 长 | 短 |

| 项目 | 旧方案 | 新方案 |
| --- | --- | --- |
| 刚度 | 低 | 高 |
| 维护 | 复杂 | 简单 |
:::
"""
    slides = parse_pptx_slides(md.split(":::artifact", 1)[-1].split("\n", 1)[-1].rsplit(":::", 1)[0], deck_title="多表切分")
    # 至少封面/主表 + 续表
    table_slides = [s for s in slides if s.table_data]
    assert len(table_slides) >= 2
    # 主表与续表列数各自独立，禁止硬拼成 3 列空尾巴
    first = table_slides[0].table_data
    second = table_slides[1].table_data
    assert first is not None and second is not None
    assert all(len(r) == len(first[0]) for r in first)
    assert all(len(r) == len(second[0]) for r in second)
    # 续表标题带「续表」
    assert "续表" in table_slides[1].title
    # 第一张表不应吞入第二张表的表头
    first_flat = " ".join(c for row in first for c in row)
    assert "旧方案" not in first_flat


def test_same_page_multi_table_via_extract_artifact():
    md = """
:::artifact type="pptx" title="多表"
---
# 页一
| A | B |
| --- | --- |
| 1 | 2 |

| C | D |
| --- | --- |
| 3 | 4 |
:::
"""
    artifact = extract_artifact(md, default_type="pptx")
    # 单页多表 → 拆成主表页 + 续表页（无封面时至少 2 张）
    tables = [s.table_data for s in artifact.slides if s.table_data]
    assert len(tables) >= 2
    assert len(artifact.slides) >= 2
