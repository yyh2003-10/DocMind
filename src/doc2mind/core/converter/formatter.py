"""格式互转器 — DocumentElement → MD / JSON / TXT / HTML。

设计：
- `MarkdownFormatter` 保留标题层级 / 表格 / 列表 / 代码块
- `JsonFormatter` 输出标准 JSON（元素数组）
- `TextFormatter` 纯文本（去标记，按元素顺序）
- `HtmlFormatter` 输出最小 HTML5 文档

入口：
    convert_document(doc, output_format) -> str
    convert_elements(elements, source, output_format) -> str
"""

from __future__ import annotations

import html as html_lib
import json
from collections.abc import Sequence

from doc2mind.core.models import DocumentElement, LoadedDocument


class ConversionError(Exception):
    """转换异常。"""


# --- 格式常量 ---
FORMAT_MARKDOWN = "md"
FORMAT_JSON = "json"
FORMAT_TEXT = "txt"
FORMAT_HTML = "html"

SUPPORTED_FORMATS = (FORMAT_MARKDOWN, FORMAT_JSON, FORMAT_TEXT, FORMAT_HTML)


# --- 入口 ---
def convert_document(doc: LoadedDocument, output_format: str) -> str:
    """转换整篇文档。

    Args:
        doc: 已加载文档
        output_format: md / json / txt / html

    Returns:
        目标格式字符串
    """
    return convert_elements(
        elements=doc.elements,
        source=doc.source,
        output_format=output_format,
    )


def convert_elements(
    elements: Sequence[DocumentElement],
    source: str,
    output_format: str,
) -> str:
    """转换元素列表到目标格式。

    Args:
        elements: `DocumentElement` 序列
        source: 源文件名（用于 HTML title / JSON meta）
        output_format: md / json / txt / html

    Returns:
        目标格式字符串

    Raises:
        ConversionError: 不支持的格式
    """
    fmt = output_format.lower().strip()
    if fmt not in SUPPORTED_FORMATS:
        raise ConversionError(
            f"不支持的输出格式: {output_format!r}，支持 {SUPPORTED_FORMATS}"
        )

    if fmt == FORMAT_MARKDOWN:
        return _to_markdown(elements)
    if fmt == FORMAT_JSON:
        return _to_json(elements, source)
    if fmt == FORMAT_TEXT:
        return _to_text(elements)
    if fmt == FORMAT_HTML:
        return _to_html(elements, source)
    raise ConversionError(f"未知格式: {fmt}")  # 不可达


# --- Markdown ---
def _to_markdown(elements: Sequence[DocumentElement]) -> str:
    """渲染为 GitHub-flavored Markdown。"""
    lines: list[str] = []
    for el in elements:
        t = el.type.value
        if t == "heading":
            level = int(el.metadata.get("level", 1))
            level = max(1, min(6, level))
            lines.append(f"{'#' * level} {el.content}")
            lines.append("")
        elif t == "paragraph" or t == "table":
            lines.append(el.content)
            lines.append("")
        elif t == "table_row":
            lines.append(el.content)
        elif t == "code":
            lang = el.metadata.get("language", "")
            lines.append(f"```{lang}")
            lines.append(el.content)
            lines.append("```")
            lines.append("")
        elif t == "list":
            lines.append(el.content)
            lines.append("")
        elif t in ("list_item",):
            lines.append(el.content)
        elif t == "image":
            alt = el.metadata.get("alt", "")
            src = el.metadata.get("src", "")
            if src:
                lines.append(f"![{alt}]({src})")
                lines.append("")
            elif alt:
                lines.append(f"_{alt}_")
                lines.append("")
        else:
            if el.content.strip():
                lines.append(el.content)
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- JSON ---
def _to_json(elements: Sequence[DocumentElement], source: str) -> str:
    """渲染为标准 JSON 数组。"""
    payload = {
        "source": source,
        "element_count": len(elements),
        "elements": [_element_to_dict(el) for el in elements],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _element_to_dict(el: DocumentElement) -> dict:
    """元素 → JSON dict。"""
    return {
        "content": el.content,
        "type": el.type.value,
        "metadata": el.metadata,
    }


# --- Text ---
def _to_text(elements: Sequence[DocumentElement]) -> str:
    """渲染为纯文本（去标记）。"""
    lines: list[str] = []
    for el in elements:
        t = el.type.value
        if t == "heading":
            lines.append(el.content)
            lines.append("")
        elif t in ("paragraph", "table", "list", "list_item", "image"):
            # 表格 / 列表的 content 已是纯文本
            text = el.content.replace("|", " ").strip()
            if text:
                lines.append(text)
                lines.append("")
        elif t == "code":
            lines.append(el.content)
            lines.append("")
        elif t == "table_row":
            # 跳过 table_row 的标记，取单元格
            cells = [c.strip() for c in el.content.strip("|").split("|")]
            lines.append("\t".join(cells))
        else:
            if el.content.strip():
                lines.append(el.content)
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- HTML ---
def _to_html(elements: Sequence[DocumentElement], source: str) -> str:
    """渲染为最小 HTML5 文档。"""
    title = html_lib.escape(source)
    body_parts: list[str] = []
    for el in elements:
        body_parts.append(_element_to_html(el))
    body = "\n".join(body_parts)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{title}</title>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _element_to_html(el: DocumentElement) -> str:
    """元素 → HTML 片段。"""
    t = el.type.value
    safe = html_lib.escape(el.content)
    if t == "heading":
        level = int(el.metadata.get("level", 1))
        level = max(1, min(6, level))
        return f"<h{level}>{safe}</h{level}>"
    if t == "table":
        # 直接渲染 markdown 表格的 HTML 等价物
        return _markdown_table_to_html(el.content)
    if t == "code":
        lang = el.metadata.get("language", "")
        cls = f' class="language-{html_lib.escape(lang)}"' if lang else ""
        return f"<pre><code{cls}>{safe}</code></pre>"
    if t == "list":
        items = "\n".join(
            f"<li>{html_lib.escape(it)}</li>"
            for it in el.content.splitlines()
            if it.strip()
        )
        tag = "ol" if el.metadata.get("ordered") else "ul"
        return f"<{tag}>{items}</{tag}>"
    if t == "image":
        alt = html_lib.escape(el.metadata.get("alt", ""))
        src = html_lib.escape(el.metadata.get("src", ""))
        if src:
            return f'<img src="{src}" alt="{alt}">'
        return f"<p><em>{alt}</em></p>"
    # paragraph / list_item / table_row / unknown
    return f"<p>{safe}</p>"


def _markdown_table_to_html(md_table: str) -> str:
    """把 markdown 表格转 HTML `<table>`。"""
    lines = [ln for ln in md_table.splitlines() if ln.strip()]
    if len(lines) < 2:
        return f"<pre>{html_lib.escape(md_table)}</pre>"
    # 第一行表头，第二行 |---|---| 跳过，其余 body
    header_cells = [c.strip() for c in lines[0].strip("|").split("|")]
    body_rows = lines[2:]
    parts = ["<table>"]
    parts.append("<thead><tr>")
    parts.extend(f"<th>{html_lib.escape(c)}</th>" for c in header_cells)
    parts.append("</tr></thead><tbody>")
    for row in body_rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        parts.append("<tr>")
        parts.extend(f"<td>{html_lib.escape(c)}</td>" for c in cells)
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)
