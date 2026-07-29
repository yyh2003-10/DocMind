"""文件格式检测与 Loader 路由。

按扩展名映射到对应 Loader 实例：
    .pdf            → PdfLoader (pdfminer.six, core)
    .docx           → DocxLoader (python-docx, core)
    .xlsx           → XlsxLoader (openpyxl, core)
    .pptx           → PptxLoader (python-pptx, core)
    .md/.markdown   → MarkdownLoader (markdown-it-py, core)
    .html/.htm      → HtmlLoader (bs4 + lxml, core)
    .png/.jpg/.jpeg/.bmp/.webp/.tiff
                    → ImageLoader (PaddleOCR, extras)
    .py/.js/.ts/.java/.c/.cpp/.go/.rs/.sh/.rb/.php/...
                    → CodeLoader (纯 Python, core)

未识别的扩展名 → DocFormat.UNKNOWN，调用方决定是否拒绝。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from doc2mind.core.loader.base import Loader, UnsupportedFormatError
from doc2mind.core.models import DocFormat


# --- 扩展名 → DocFormat 映射 ---
_EXTENSION_MAP: dict[str, DocFormat] = {
    # PDF
    "pdf": DocFormat.PDF,
    # Office
    "docx": DocFormat.DOCX,
    "doc": DocFormat.DOCX,  # 老格式暂当作 docx 尝试
    "xlsx": DocFormat.XLSX,
    "xls": DocFormat.XLSX,
    "pptx": DocFormat.PPTX,
    "ppt": DocFormat.PPTX,
    # Markdown
    "md": DocFormat.MARKDOWN,
    "markdown": DocFormat.MARKDOWN,
    "mdx": DocFormat.MARKDOWN,
    # HTML
    "html": DocFormat.HTML,
    "htm": DocFormat.HTML,
    "xhtml": DocFormat.HTML,
    # 图片
    "png": DocFormat.IMAGE,
    "jpg": DocFormat.IMAGE,
    "jpeg": DocFormat.IMAGE,
    "bmp": DocFormat.IMAGE,
    "webp": DocFormat.IMAGE,
    "tif": DocFormat.IMAGE,
    "tiff": DocFormat.IMAGE,
    "gif": DocFormat.IMAGE,
}

# --- 代码语言映射 ---
_CODE_EXTENSIONS: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "java": "java",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cxx": "cpp",
    "cc": "cpp",
    "hpp": "cpp",
    "hxx": "cpp",
    "cs": "csharp",
    "go": "go",
    "rs": "rust",
    "rb": "ruby",
    "php": "php",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "ps1": "powershell",
    "sql": "sql",
    "swift": "swift",
    "kt": "kotlin",
    "kts": "kotlin",
    "scala": "scala",
    "sc": "scala",
    "r": "r",
    "lua": "lua",
    "pl": "perl",
    "vim": "vim",
    "yaml": "yaml",
    "yml": "yaml",
    "json": "json",
    "xml": "xml",
    "ini": "ini",
    "toml": "toml",
    "cfg": "ini",
    "conf": "ini",
    "dockerfile": "dockerfile",
    "makefile": "makefile",
}


def detect_format(path: Path) -> DocFormat:
    """按扩展名检测文档格式。

    Args:
        path: 文件路径

    Returns:
        `DocFormat`；未识别返回 `DocFormat.UNKNOWN`。
    """
    # 优先按无后缀文件名（Dockerfile / Makefile）
    stem_lower = path.stem.lower()
    if stem_lower in _CODE_EXTENSIONS:
        return DocFormat.CODE

    ext = path.suffix.lower().lstrip(".")
    if ext in _EXTENSION_MAP:
        return _EXTENSION_MAP[ext]
    if ext in _CODE_EXTENSIONS:
        return DocFormat.CODE
    return DocFormat.UNKNOWN


def is_supported(path: Path) -> bool:
    """判断该文件是否被任何 loader 支持。"""
    return detect_format(path) != DocFormat.UNKNOWN


# --- Loader 工厂（惰性 import 避免冷启动加载全部依赖）---
@lru_cache(maxsize=1)
def _loaders() -> dict[DocFormat, Loader]:
    """初始化所有 loader，按 `DocFormat` 索引。"""
    from doc2mind.core.loader.code_loader import CodeLoader
    from doc2mind.core.loader.docx_loader import DocxLoader
    from doc2mind.core.loader.html_loader import HtmlLoader
    from doc2mind.core.loader.image_loader import ImageLoader
    from doc2mind.core.loader.md_loader import MarkdownLoader
    from doc2mind.core.loader.pdf_loader import PdfLoader
    from doc2mind.core.loader.pptx_loader import PptxLoader
    from doc2mind.core.loader.xlsx_loader import XlsxLoader

    return {
        DocFormat.PDF: PdfLoader(),
        DocFormat.DOCX: DocxLoader(),
        DocFormat.XLSX: XlsxLoader(),
        DocFormat.PPTX: PptxLoader(),
        DocFormat.MARKDOWN: MarkdownLoader(),
        DocFormat.HTML: HtmlLoader(),
        DocFormat.IMAGE: ImageLoader(),
        DocFormat.CODE: CodeLoader(),
    }


def get_loader(path: Path) -> Loader:
    """按路径扩展名返回对应 Loader 实例。

    Args:
        path: 文件路径

    Returns:
        `Loader` 实例

    Raises:
        UnsupportedFormatError: 扩展名不在支持列表
    """
    fmt = detect_format(path)
    if fmt is DocFormat.UNKNOWN:
        raise UnsupportedFormatError(
            f"不支持的文件格式: {path.suffix or '(无扩展名)'} ({path.name})"
        )
    return _loaders()[fmt]
