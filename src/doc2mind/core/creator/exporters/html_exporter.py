"""HTML 原生单文件看板与交互式幻灯片放映引擎 (Web SlideShow Engine)。

特性：
- 100% 离线自包含，无需任何外部 CDN / JS 库依赖；
- 针对 PPT 自动生成 16:9 宽屏交互式放映网页（键盘左右键/空格翻页、全屏放映、演讲备注抽屉）；
- 完美适配 5 套企业主题配色；
- 针对普通研报生成高颜值响应式知识看板。
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path

from doc2mind.core.creator.models import ArtifactModel, ArtifactType, SlideLayoutType
from doc2mind.core.creator.themes import get_theme

logger = logging.getLogger("doc2mind.creator.html")

_SLIDESHOW_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — DocMind SlideShow</title>
    <style>
        :root {{
            --primary: {primary_hex};
            --primary-light: {primary_light_hex};
            --secondary: {secondary_hex};
            --accent: {accent_hex};
            --bg-body: {bg_body_hex};
            --bg-card: {bg_card_hex};
            --text-title: {text_title_hex};
            --text-body: {text_body_hex};
            --text-muted: {text_muted_hex};
            --border-color: {border_hex};
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
            background-color: var(--bg-body);
            color: var(--text-body);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            overflow: hidden;
            user-select: none;
        }}
        /* 16:9 幻灯片放映舞台 */
        .stage-wrapper {{
            position: relative;
            width: 90vw;
            max-width: 1200px;
            aspect-ratio: 16 / 9;
            background: var(--bg-card);
            border-radius: 16px;
            border: 1.5px solid var(--border-color);
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.12);
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }}
        .slide-container {{
            flex: 1;
            padding: 48px 64px;
            position: relative;
            overflow-y: auto;
            display: none;
            flex-direction: column;
        }}
        .slide-container.active {{
            display: flex;
            animation: fadeIn 0.25s ease-out;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(6px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}

        /* 顶部装饰条与板式标 */
        .top-bar {{
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 6px;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
        }}
        .layout-tag {{
            align-self: flex-start;
            font-size: 11px;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 4px;
            background: var(--primary-light);
            color: var(--primary);
            margin-bottom: 12px;
            text-transform: uppercase;
        }}
        .slide-title {{
            font-size: 32px;
            font-weight: 800;
            color: var(--text-title);
            margin-bottom: 24px;
            line-height: 1.3;
        }}
        .slide-subtitle {{
            font-size: 20px;
            color: var(--text-muted);
            margin-bottom: 32px;
        }}

        /* 板式：封面 */
        .cover-layout {{
            justify-content: center;
            align-items: center;
            text-align: center;
            height: 100%;
        }}
        .cover-layout .slide-title {{
            font-size: 44px;
            margin-bottom: 16px;
        }}

        /* 板式：多列卡片 */
        .cards-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-top: 10px;
        }}
        .card-item {{
            background: var(--primary-light);
            border-radius: 12px;
            padding: 24px;
            border: 1px solid var(--border-color);
            box-shadow: 0 4px 12px rgba(0,0,0,0.03);
        }}
        .card-item h3 {{
            color: var(--primary);
            font-size: 18px;
            margin-bottom: 12px;
        }}
        .card-item ul {{
            padding-left: 18px;
            font-size: 14px;
            line-height: 1.6;
        }}

        /* 板式：大数字看板 */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-top: 16px;
        }}
        .metric-card {{
            background: var(--primary-light);
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            border-top: 4px solid var(--primary);
        }}
        .metric-val {{
            font-size: 40px;
            font-weight: 900;
            color: var(--primary);
            margin-bottom: 6px;
        }}
        .metric-lbl {{
            font-size: 16px;
            font-weight: 700;
            color: var(--text-title);
        }}

        /* 板式：时间线 */
        .timeline-container {{
            display: flex;
            gap: 16px;
            margin-top: 24px;
            overflow-x: auto;
        }}
        .timeline-node {{
            flex: 1;
            background: var(--primary-light);
            border-radius: 10px;
            padding: 16px;
            border-top: 4px solid var(--accent);
        }}
        .timeline-stage {{
            font-size: 12px;
            font-weight: 800;
            color: var(--primary);
            margin-bottom: 6px;
        }}
        .timeline-title {{
            font-size: 15px;
            font-weight: 700;
            color: var(--text-title);
        }}

        /* 板式：金句 */
        .quote-box {{
            background: var(--primary-light);
            border-left: 6px solid var(--primary);
            padding: 32px 40px;
            border-radius: 0 16px 16px 0;
            margin: auto 0;
            font-size: 22px;
            font-style: italic;
            font-weight: 700;
            color: var(--text-title);
            line-height: 1.6;
        }}

        /* 板式：表格 */
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
            font-size: 15px;
        }}
        th, td {{
            padding: 12px 16px;
            border: 1px solid var(--border-color);
            text-align: left;
        }}
        th {{
            background: var(--primary);
            color: #ffffff;
            font-weight: 700;
        }}
        tr:nth-child(even) {{ background: var(--primary-light); }}

        /* 底部控制栏 */
        .controls-bar {{
            height: 56px;
            background: rgba(0, 0, 0, 0.03);
            border-top: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 24px;
            font-size: 13px;
        }}
        .btn {{
            background: var(--primary);
            color: #ffffff;
            border: none;
            padding: 6px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            transition: opacity 0.2s;
        }}
        .btn:hover {{ opacity: 0.9; }}
        .btn:disabled {{ opacity: 0.3; cursor: not-allowed; }}
        .btn-ghost {{
            background: transparent;
            color: var(--text-body);
            border: 1px solid var(--border-color);
        }}

        /* 演讲备注提词器抽屉 */
        .notes-drawer {{
            position: fixed;
            bottom: 0; left: 0; right: 0;
            background: rgba(15, 23, 42, 0.95);
            color: #f8fafc;
            padding: 16px 32px;
            font-size: 14px;
            backdrop-filter: blur(8px);
            transform: translateY(100%);
            transition: transform 0.3s ease;
            max-height: 160px;
            overflow-y: auto;
            z-index: 100;
        }}
        .notes-drawer.open {{
            transform: translateY(0);
        }}
        .notes-title {{
            font-size: 12px;
            font-weight: 800;
            color: #38bdf8;
            margin-bottom: 4px;
        }}
    </style>
</head>
<body>
    <div class="stage-wrapper">
        <div class="top-bar"></div>
        {slides_html}
        <div class="controls-bar">
            <div>
                <button class="btn btn-ghost" id="prevBtn" onclick="prevSlide()">◀ 上一页</button>
                <button class="btn" id="nextBtn" onclick="nextSlide()" style="margin-left: 8px;">下一页 ▶</button>
            </div>
            <div id="pageIndicator" style="font-weight: 700; color: var(--primary);">1 / {total_slides}</div>
            <div>
                <button class="btn btn-ghost" onclick="toggleNotes()">🎤 演讲提词 (N)</button>
                <button class="btn btn-ghost" onclick="toggleFullScreen()" style="margin-left: 8px;">⛶ 全屏 (F)</button>
            </div>
        </div>
    </div>

    <div class="notes-drawer" id="notesDrawer">
        <div class="notes-title">🎤 演讲者实时提词小抄 (按 N 键收起/展开)</div>
        <div id="notesText"></div>
    </div>

    <script>
        const notes = {notes_json};
        let currentIdx = 0;
        const total = {total_slides};

        function showSlide(idx) {{
            if (idx < 0 || idx >= total) return;
            currentIdx = idx;
            document.querySelectorAll('.slide-container').forEach((el, i) => {{
                el.classList.toggle('active', i === idx);
            }});
            document.getElementById('pageIndicator').innerText = `${{idx + 1}} / ${{total}}`;
            document.getElementById('prevBtn').disabled = idx === 0;
            document.getElementById('nextBtn').disabled = idx === total - 1;

            const nText = notes[idx] || "（本页暂无演讲备注）";
            document.getElementById('notesText').innerText = nText;
        }}

        function prevSlide() {{ if (currentIdx > 0) showSlide(currentIdx - 1); }}
        function nextSlide() {{ if (currentIdx < total - 1) showSlide(currentIdx + 1); }}

        function toggleNotes() {{
            document.getElementById('notesDrawer').classList.toggle('open');
        }}

        function toggleFullScreen() {{
            if (!document.fullscreenElement) {{
                document.documentElement.requestFullscreen().catch(() => {{}});
            }} else {{
                document.exitFullscreen().catch(() => {{}});
            }}
        }}

        window.addEventListener('keydown', (e) => {{
            if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown') {{
                nextSlide();
            }} else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {{
                prevSlide();
            }} else if (e.key === 'f' || e.key === 'F') {{
                toggleFullScreen();
            }} else if (e.key === 'n' || e.key === 'N') {{
                toggleNotes();
            }}
        }});

        showSlide(0);
    </script>
</body>
</html>
"""


class HtmlExporter:
    """自包含 HTML 看板与 SlideShow 放映导出器。"""

    def __init__(self) -> None:
        pass

    def export(self, artifact: ArtifactModel, output_path: Path) -> Path:
        """编译生成 .html 交互式放映或看板文件。"""

        theme = get_theme(artifact.theme)

        # 若为 PPTX 格式，生成高保真交互式 SlideShow 放映网页
        if artifact.artifact_type == ArtifactType.PPTX and artifact.slides:
            slides_html_list: list[str] = []
            notes_list: list[str] = []

            for idx, s in enumerate(artifact.slides):
                notes_list.append(s.speaker_notes)
                s_body = []
                layout_tag_text = s.layout.value.upper()

                if s.layout == SlideLayoutType.COVER:
                    s_body.append(f"""
                    <div class="cover-layout">
                        <div class="layout-tag">COVER</div>
                        <div class="slide-title">{html.escape(s.title)}</div>
                        <div class="slide-subtitle">{html.escape(s.subtitle or artifact.description or "")}</div>
                    </div>
                    """)
                elif s.layout == SlideLayoutType.CARDS and s.cards:
                    cards_h = []
                    for c in s.cards:
                        b_items = "".join(f"<li>{html.escape(b)}</li>" for b in c.bullets)
                        cards_h.append(f"""
                        <div class="card-item">
                            <h3>{html.escape(c.title)}</h3>
                            {f'<p style="margin-bottom:8px;font-size:13px;">{html.escape(c.content)}</p>' if c.content else ''}
                            {f'<ul>{b_items}</ul>' if b_items else ''}
                        </div>
                        """)
                    s_body.append(f"""
                    <div class="layout-tag">CARDS GRID</div>
                    <div class="slide-title">{html.escape(s.title)}</div>
                    <div class="cards-grid">{''.join(cards_h)}</div>
                    """)
                elif s.layout == SlideLayoutType.METRICS and s.metrics:
                    metrics_h = []
                    for m in s.metrics:
                        metrics_h.append(f"""
                        <div class="metric-card">
                            <div class="metric-val">{html.escape(m.value)}</div>
                            <div class="metric-lbl">{html.escape(m.label)}</div>
                        </div>
                        """)
                    s_body.append(f"""
                    <div class="layout-tag">METRICS KPI</div>
                    <div class="slide-title">{html.escape(s.title)}</div>
                    <div class="metrics-grid">{''.join(metrics_h)}</div>
                    """)
                elif s.layout == SlideLayoutType.TIMELINE and s.timeline_nodes:
                    timeline_h = []
                    for t in s.timeline_nodes:
                        timeline_h.append(f"""
                        <div class="timeline-node">
                            <div class="timeline-stage">{html.escape(t.stage)}</div>
                            <div class="timeline-title">{html.escape(t.title)}</div>
                        </div>
                        """)
                    s_body.append(f"""
                    <div class="layout-tag">TIMELINE ROADMAP</div>
                    <div class="slide-title">{html.escape(s.title)}</div>
                    <div class="timeline-container">{''.join(timeline_h)}</div>
                    """)
                elif s.layout == SlideLayoutType.QUOTE and s.quote_text:
                    s_body.append(f"""
                    <div class="layout-tag">KEY CONCLUSION</div>
                    <div class="slide-title">{html.escape(s.title)}</div>
                    <div class="quote-box">“ {html.escape(s.quote_text)} ”</div>
                    """)
                elif s.layout == SlideLayoutType.TABLE and s.table_data:
                    table_h = ["<table>"]
                    header = True
                    for row in s.table_data:
                        if header:
                            table_h.append("<thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in row) + "</tr></thead><tbody>")
                            header = False
                        else:
                            table_h.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>")
                    table_h.append("</tbody></table>")
                    s_body.append(f"""
                    <div class="layout-tag">COMPARISON MATRIX</div>
                    <div class="slide-title">{html.escape(s.title)}</div>
                    {''.join(table_h)}
                    """)
                else:
                    # 普通列表
                    bullets_h = "".join(f"<li style='margin-bottom:8px;font-size:16px;'>{html.escape(b)}</li>" for b in s.bullet_points)
                    s_body.append(f"""
                    <div class="layout-tag">{layout_tag_text}</div>
                    <div class="slide-title">{html.escape(s.title)}</div>
                    {f'<div class="slide-subtitle">{html.escape(s.subtitle)}</div>' if s.subtitle else ''}
                    <ul style="padding-left: 28px; line-height: 1.8;">{bullets_h}</ul>
                    """)

                slides_html_list.append(f'<div class="slide-container" id="slide-{idx}">{"".join(s_body)}</div>')

            full_html = _SLIDESHOW_TEMPLATE.format(
                title=html.escape(artifact.title or "DocMind 演示文稿"),
                primary_hex=theme.primary.hex,
                primary_light_hex=theme.primary_light.hex,
                secondary_hex=theme.secondary.hex,
                accent_hex=theme.accent.hex,
                bg_body_hex="#0f172a" if artifact.theme == "dark_elegant" else "#f1f5f9",
                bg_card_hex=theme.card_bg.hex,
                text_title_hex=theme.text_title.hex,
                text_body_hex=theme.text_body.hex,
                text_muted_hex=theme.text_muted.hex,
                border_hex="#334155" if artifact.theme == "dark_elegant" else "#e2e8f0",
                slides_html="\n".join(slides_html_list),
                total_slides=len(artifact.slides),
                notes_json=json.dumps(notes_list, ensure_ascii=False),
            )
        else:
            # 基础研报看板
            lines = artifact.raw_content.splitlines()
            html_blocks: list[str] = []
            in_table = False
            table_lines: list[str] = []
            in_list = False
            list_items: list[str] = []

            def flush_table() -> None:
                nonlocal in_table, table_lines
                if not table_lines:
                    in_table = False
                    return
                parsed_rows: list[list[str]] = []
                for t_line in table_lines:
                    if re.match(r"^\|[\s\-:|]+\|$", t_line.strip()):
                        continue
                    cols = [c.strip() for c in t_line.strip().strip("|").split("|")]
                    if any(cols):
                        parsed_rows.append(cols)
                if parsed_rows:
                    t_html = ["<table>"]
                    t_html.append("<thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in parsed_rows[0]) + "</tr></thead>")
                    if len(parsed_rows) > 1:
                        t_html.append("<tbody>")
                        for r_idx, row in enumerate(parsed_rows[1:]):
                            bg_style = f" style='background:{theme.primary_light.hex};'" if r_idx % 2 == 1 else ""
                            t_html.append(f"<tr{bg_style}>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>")
                        t_html.append("</tbody>")
                    t_html.append("</table>")
                    html_blocks.append("".join(t_html))
                table_lines = []
                in_table = False

            def flush_list() -> None:
                nonlocal in_list, list_items
                if not list_items:
                    in_list = False
                    return
                html_blocks.append("<ul style='padding-left:24px;margin-bottom:12px;line-height:1.8;'>" + "".join(f"<li style='margin-bottom:4px;'>{html.escape(item)}</li>" for item in list_items) + "</ul>")
                list_items = []
                in_list = False

            for line in lines:
                ls = line.strip()
                if not ls:
                    if in_table:
                        flush_table()
                    if in_list:
                        flush_list()
                    continue

                if ls.startswith("|") and ls.endswith("|"):
                    if in_list:
                        flush_list()
                    in_table = True
                    table_lines.append(ls)
                    continue
                elif in_table:
                    flush_table()

                if ls.startswith(("- ", "* ", "+ ")):
                    in_list = True
                    list_items.append(ls[2:].strip())
                    continue
                elif in_list:
                    flush_list()

                if ls.startswith("# "):
                    html_blocks.append(f"<h1 style='color:{theme.primary.hex};margin:24px 0 12px;border-bottom:2px solid {theme.primary.hex};padding-bottom:8px;'>{html.escape(ls[2:])}</h1>")
                elif ls.startswith("## "):
                    html_blocks.append(f"<h2 style='color:{theme.primary.hex};margin:20px 0 10px;'>{html.escape(ls[3:])}</h2>")
                elif ls.startswith("### "):
                    html_blocks.append(f"<h3 style='color:{theme.text_title.hex};margin:14px 0 8px;'>{html.escape(ls[4:])}</h3>")
                elif ls.startswith("> "):
                    html_blocks.append(f"<blockquote style='background:{theme.primary_light.hex};border-left:4px solid {theme.primary.hex};padding:12px 16px;margin:14px 0;border-radius:0 8px 8px 0;'>{html.escape(ls[2:])}</blockquote>")
                else:
                    html_blocks.append(f"<p style='margin-bottom:8px;'>{html.escape(ls)}</p>")

            if in_table:
                flush_table()
            if in_list:
                flush_list()

            full_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{html.escape(artifact.title)}</title>
<style>
body{{font-family:sans-serif;padding:32px;background:{theme.bg.hex};color:{theme.text_body.hex};line-height:1.6;}}
.container{{max-width:800px;margin:auto;background:#fff;padding:32px;border-radius:12px;box-shadow:0 4px 16px rgba(0,0,0,0.06);}}
table{{width:100%;border-collapse:collapse;margin:16px 0;font-size:14px;}}
th,td{{border:1px solid #e2e8f0;padding:10px 14px;text-align:left;}}
th{{background:{theme.primary.hex};color:#ffffff;font-weight:700;}}
</style>
</head>
<body><div class="container"><h1 style="color:{theme.primary.hex};border-bottom:2px solid {theme.primary.hex};padding-bottom:12px;">{html.escape(artifact.title)}</h1>{''.join(html_blocks)}</div></body>
</html>"""

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(full_html, encoding="utf-8")
        logger.info("HTML 导出成功: %s", output_path)
        return output_path
