"""Markdown 加载器 — 基于 `markdown-it-py` 的 token stream。

不直接读取 Markdown 文本（无法精确区分结构层级），
而是用 markdown-it 的 `token` 序列提取标题 / 段落 / 列表 / 代码块。

支持特性：
- ATX 标题（# ~ ######）→ heading
- Setext 标题（下划 === / ---）→ heading
- 围栏代码块（``` / ~~~）→ code（带 language）
- 缩进代码块 → code
- 列表项 → list / list_item
- 表格（GFM）→ table
- 段落 → paragraph
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from doc2mind.core.loader.base import Loader, LoaderError
from doc2mind.core.models import (
    DocFormat,
    DocumentElement,
    ElementType,
    LoadedDocument,
)


class MarkdownLoader(Loader):
    """Markdown 文档加载器（markdown-it-py token stream）。"""

    supported_extensions = ("md", "markdown", "mdx")

    def extract(self, path: Path) -> LoadedDocument:
        try:
            from markdown_it import MarkdownIt
        except ImportError as e:
            raise LoaderError(
                "markdown-it-py 未安装。请运行：pip install markdown-it-py"
            ) from e

        if not path.exists():
            raise LoaderError(f"文件不存在: {path}")

        try:
            data = path.read_bytes()
            file_hash = hashlib.md5(data).hexdigest()
            # 启用表格、任务列表等 GFM 扩展
            md = MarkdownIt("commonmark", {"html": True}).enable("table")
            text = data.decode("utf-8", errors="replace")
            tokens = md.parse(text, {})

            elements: list[DocumentElement] = []
            i = 0
            n = len(tokens)
            while i < n:
                tok = tokens[i]
                ttype = tok.type

                if ttype == "heading_open":
                    # 下一个 token 是 inline，包含标题文本
                    level = int(tok.tag[1]) if tok.tag.startswith("h") else 1
                    inline_tok = tokens[i + 1] if i + 1 < n else None
                    content = (
                        inline_tok.content.strip() if inline_tok and inline_tok.content else ""
                    )
                    if content:
                        elements.append(
                            DocumentElement(
                                content=content,
                                type=ElementType.HEADING,
                                metadata={
                                    "type": "heading",
                                    "level": level,
                                    "source_format": DocFormat.MARKDOWN.value,
                                },
                            )
                        )
                    # 跳过 inline + heading_close
                    i += 3 if i + 2 < n and tokens[i + 2].type == "heading_close" else 2
                    continue

                if ttype == "paragraph_open":
                    inline_tok = tokens[i + 1] if i + 1 < n else None
                    content = (
                        inline_tok.content.strip() if inline_tok and inline_tok.content else ""
                    )
                    if content:
                        elements.append(
                            DocumentElement(
                                content=content,
                                type=ElementType.PARAGRAPH,
                                metadata={
                                    "type": "paragraph",
                                    "source_format": DocFormat.MARKDOWN.value,
                                },
                            )
                        )
                    i += 3 if i + 2 < n and tokens[i + 2].type == "paragraph_close" else 2
                    continue

                if ttype == "fence" or ttype == "code_block":
                    # fence 带 info（语言），code_block 为缩进代码
                    lang = (tok.info or "").strip() if ttype == "fence" else ""
                    content = (tok.content or "").strip()
                    if content:
                        elements.append(
                            DocumentElement(
                                content=content,
                                type=ElementType.CODE,
                                metadata={
                                    "type": "code",
                                    "language": lang or "text",
                                    "source_format": DocFormat.MARKDOWN.value,
                                },
                            )
                        )
                    i += 1
                    continue

                if ttype == "table_open":
                    # 收集表格内所有 inline content 拼成 markdown 文本
                    table_md = _extract_table(tokens, i)
                    if table_md:
                        elements.append(
                            DocumentElement(
                                content=table_md,
                                type=ElementType.TABLE,
                                metadata={
                                    "type": "table",
                                    "source_format": DocFormat.MARKDOWN.value,
                                },
                            )
                        )
                    # 跳到 table_close 之后
                    j = i + 1
                    while j < n and tokens[j].type != "table_close":
                        j += 1
                    i = j + 1
                    continue

                if ttype == "bullet_list_open" or ttype == "ordered_list_open":
                    list_items = _extract_list_items(tokens, i)
                    if list_items:
                        # 整体作为 LIST 元素，content 为 markdown 列表
                        md_text = "\n".join(
                            f"- {item}" if ttype == "bullet_list_open" else f"1. {item}"
                            for item in list_items
                        )
                        elements.append(
                            DocumentElement(
                                content=md_text,
                                type=ElementType.LIST,
                                metadata={
                                    "type": "list",
                                    "ordered": ttype == "ordered_list_open",
                                    "item_count": len(list_items),
                                    "source_format": DocFormat.MARKDOWN.value,
                                },
                            )
                        )
                    j = i + 1
                    end_type = "bullet_list_close" if ttype == "bullet_list_open" else "ordered_list_close"
                    while j < n and tokens[j].type != end_type:
                        j += 1
                    i = j + 1
                    continue

                # 其他 token 跳过
                i += 1

            return LoadedDocument(
                source=path.name,
                format=DocFormat.MARKDOWN,
                elements=elements,
                page_count=None,
                size_bytes=len(data),
                file_hash=file_hash,
            )
        except LoaderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LoaderError(f"Markdown 解析失败 ({path.name}): {e}") from e


def _extract_table(tokens, start: int) -> str:
    """从 `table_open` 开始提取表格内容，转成 Markdown 文本。"""
    rows: list[list[str]] = []
    current_row: list[str] = []
    in_cell = False
    i = start + 1
    while i < len(tokens):
        t = tokens[i]
        if t.type == "table_close":
            break
        if t.type == "tr_open":
            current_row = []
        elif t.type == "tr_close":
            if current_row:
                rows.append(current_row)
        elif t.type in ("th_open", "td_open"):
            in_cell = True
        elif t.type in ("th_close", "td_close"):
            in_cell = False
        elif t.type == "inline" and in_cell:
            current_row.append((t.content or "").strip())
        i += 1

    if not rows:
        return ""
    lines: list[str] = []
    for idx, row in enumerate(rows):
        lines.append("| " + " | ".join(row) + " |")
        if idx == 0:
            lines.append("|" + "---|" * len(row))
    return "\n".join(lines)


def _extract_list_items(tokens, start: int) -> list[str]:
    """提取列表项文本。"""
    items: list[str] = []
    i = start + 1
    in_item = False
    while i < len(tokens):
        t = tokens[i]
        # list_close 是对应的 *同层* 关闭标签，遇 list_item_close 收集
        if t.type in ("bullet_list_close", "ordered_list_close"):
            break
        if t.type == "list_item_open":
            in_item = True
        elif t.type == "list_item_close":
            in_item = False
        elif t.type == "inline" and in_item:
            items.append((t.content or "").strip())
        i += 1
    return items
