"""HTML 加载器 — 基于 `beautifulsoup4` + `lxml`。

递归遍历 `<body>`，按标签映射生成 `DocumentElement`：
    h1-h6        → heading（level = N）
    p            → paragraph
    ul/ol        → list（content 为 markdown 列表）
    li           → list_item
    pre/code     → code（语言从 class 推断）
    table        → table（转 markdown）
    img          → image（保留 alt / src）
    blockquote   → paragraph（带 quote 标记）

跳过 `<script>` / `<style>` / `<nav>` / `<footer>` / `<head>`。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from doc2mind.core.loader.base import Loader, LoaderError, make_source
from doc2mind.core.models import (
    DocFormat,
    DocumentElement,
    ElementType,
    LoadedDocument,
)

# 跳过的标签
_SKIP_TAGS = frozenset({
    "script", "style", "nav", "footer", "head", "noscript", "iframe", "svg",
})

# 块级容器标签：递归遍历其子节点
_BLOCK_TAGS = frozenset({
    "div", "section", "article", "main", "aside", "header", "form", "fieldset",
})


class HtmlLoader(Loader):
    """HTML 文档加载器（bs4 + lxml）。"""

    supported_extensions = ("html", "htm", "xhtml")

    def extract(self, path: Path) -> LoadedDocument:
        try:
            from bs4 import BeautifulSoup
        except ImportError as e:
            raise LoaderError(
                "beautifulsoup4 未安装。请运行：pip install beautifulsoup4 lxml"
            ) from e

        if not path.exists():
            raise LoaderError(f"文件不存在: {path}")

        try:
            data = path.read_bytes()
            file_hash = hashlib.md5(data).hexdigest()
            # lxml 速度更快，html.parser 为 fallback
            try:
                soup = BeautifulSoup(data, "lxml")
            except Exception:  # noqa: BLE001 — lxml 缺失时降级
                soup = BeautifulSoup(data, "html.parser")

            elements: list[DocumentElement] = []
            body = soup.body or soup
            _walk(body, elements)

            return LoadedDocument(
                source=make_source(path),
                format=DocFormat.HTML,
                elements=elements,
                page_count=None,
                size_bytes=len(data),
                file_hash=file_hash,
            )
        except LoaderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LoaderError(f"HTML 解析失败 ({path.name}): {e}") from e


def _walk(node, elements: list[DocumentElement]) -> None:
    """递归遍历 DOM 节点，按标签映射生成 element。"""
    if not hasattr(node, "name") or node.name is None:
        return

    name = node.name.lower()

    if name in _SKIP_TAGS:
        return

    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        text = node.get_text(strip=True)
        if text:
            level = int(name[1])
            elements.append(
                DocumentElement(
                    content=text,
                    type=ElementType.HEADING,
                    metadata={
                        "type": "heading",
                        "level": level,
                        "source_format": DocFormat.HTML.value,
                    },
                )
            )
        return

    if name == "p":
        text = node.get_text(" ", strip=True)
        if text:
            elements.append(
                DocumentElement(
                    content=text,
                    type=ElementType.PARAGRAPH,
                    metadata={
                        "type": "paragraph",
                        "source_format": DocFormat.HTML.value,
                    },
                )
            )
        return

    if name in ("ul", "ol"):
        items: list[str] = []
        for li in node.find_all("li", recursive=False):
            t = li.get_text(" ", strip=True)
            if t:
                items.append(t)
        if items:
            marker = "- " if name == "ul" else "1. "
            md = "\n".join(f"{marker}{it}" for it in items)
            elements.append(
                DocumentElement(
                    content=md,
                    type=ElementType.LIST,
                    metadata={
                        "type": "list",
                        "ordered": name == "ol",
                        "item_count": len(items),
                        "source_format": DocFormat.HTML.value,
                    },
                )
            )
        return

    if name == "table":
        md = _table_to_markdown(node)
        if md:
            rows = len(node.find_all("tr"))
            elements.append(
                DocumentElement(
                    content=md,
                    type=ElementType.TABLE,
                    metadata={
                        "type": "table",
                        "rows": rows,
                        "source_format": DocFormat.HTML.value,
                    },
                )
            )
        return

    if name in ("pre", "code"):
        text = node.get_text()
        if text and text.strip():
            classes = node.get("class", []) or []
            lang = _infer_lang_from_classes(classes)
            elements.append(
                DocumentElement(
                    content=text.strip(),
                    type=ElementType.CODE,
                    metadata={
                        "type": "code",
                        "language": lang,
                        "source_format": DocFormat.HTML.value,
                    },
                )
            )
        return

    if name == "blockquote":
        text = node.get_text(" ", strip=True)
        if text:
            elements.append(
                DocumentElement(
                    content=text,
                    type=ElementType.PARAGRAPH,
                    metadata={
                        "type": "paragraph",
                        "quote": True,
                        "source_format": DocFormat.HTML.value,
                    },
                )
            )
        return

    if name == "img":
        alt = node.get("alt", "").strip()
        src = node.get("src", "").strip()
        if alt or src:
            elements.append(
                DocumentElement(
                    content=alt or src,
                    type=ElementType.IMAGE,
                    metadata={
                        "type": "image",
                        "alt": alt,
                        "src": src,
                        "source_format": DocFormat.HTML.value,
                    },
                )
            )
        return

    # div / section / article 等块级容器：递归子节点
    for child in node.children:
        if hasattr(child, "name"):  # 仅遍历 Tag，跳过 NavigableString
            _walk(child, elements)


def _table_to_markdown(table) -> str:
    """把 `<table>` 转 Markdown 表格字符串。"""
    rows = table.find_all("tr")
    if not rows:
        return ""
    lines: list[str] = []
    for idx, tr in enumerate(rows):
        cells = tr.find_all(["th", "td"])
        cell_texts = [c.get_text(" ", strip=True).replace("\n", " ") for c in cells]
        if not cell_texts:
            continue
        lines.append("| " + " | ".join(cell_texts) + " |")
        if idx == 0:
            lines.append("|" + "---|" * len(cell_texts))
    return "\n".join(lines)


# --- 代码语言推断 ---
_LANG_CLASS_MAP: dict[str, str] = {
    "python": "python", "py": "python",
    "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript",
    "java": "java", "c": "c", "cpp": "cpp", "c++": "cpp",
    "go": "go", "rust": "rust", "rs": "rust",
    "bash": "shell", "sh": "shell", "shell": "shell",
    "sql": "sql", "json": "json", "html": "html", "css": "css",
}


def _infer_lang_from_classes(classes: list[str]) -> str:
    """从 `class="language-python highlight-python"` 推断语言。"""
    for cls in classes:
        low = cls.lower()
        # 匹配 language-xxx 或 highlight-xxx
        for prefix in ("language-", "highlight-"):
            if low.startswith(prefix):
                lang = low[len(prefix):]
                return _LANG_CLASS_MAP.get(lang, lang)
        if low in _LANG_CLASS_MAP:
            return _LANG_CLASS_MAP[low]
    return "text"
