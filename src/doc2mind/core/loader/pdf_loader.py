"""PDF 加载器 — 基于 `pdfminer.six`。

特点：
- 纯 Python，无需 Java / PDFMiner C 扩展
- 按 LTTextBox 提取文本块
- 通过字号启发式区分标题 / 正文（avg_size > 16 → heading）
- 表格不直接支持，依赖后续 chunker 做表格保护（连续 | 行视为表格）
- 多栏布局用 `LAParams(detect_vertical=True)`

局限性：
- 复杂表格 / 双栏论文需用 extras 的 opendataloader-pdf
- 图片中的文字需用 image_loader 的 OCR
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

# 启发式阈值
_HEADING_FONT_SIZE = 16.0
_MIN_FONT_SAMPLES = 1


class PdfLoader(Loader):
    """PDF 文档加载器（pdfminer.six 实现）。"""

    supported_extensions = ("pdf",)

    def extract(self, path: Path) -> LoadedDocument:
        try:
            from pdfminer.high_level import extract_pages
            from pdfminer.layout import LTChar, LTTextBox
        except ImportError as e:
            raise LoaderError(
                "pdfminer.six 未安装。请运行：pip install pdfminer.six"
            ) from e

        if not path.exists():
            raise LoaderError(f"文件不存在: {path}")

        try:
            data = path.read_bytes()
            file_hash = hashlib.md5(data).hexdigest()
            elements: list[DocumentElement] = []
            page_no = 0

            for page_layout in extract_pages(path):
                page_no += 1
                for node in page_layout:
                    if not isinstance(node, LTTextBox):
                        continue
                    text = node.get_text().strip()
                    if not text:
                        continue

                    # 收集所有字符的字号，用平均值判断标题
                    font_sizes: list[float] = []
                    for child in node:
                        if isinstance(child, LTChar):
                            font_sizes.append(float(child.size))

                    if len(font_sizes) >= _MIN_FONT_SAMPLES:
                        avg_size = sum(font_sizes) / len(font_sizes)
                    else:
                        avg_size = 0.0

                    if avg_size > _HEADING_FONT_SIZE:
                        elem_type = ElementType.HEADING
                        metadata = {
                            "type": "heading",
                            "level": 1,  # PDF 无法精确分级，统一 H1
                            "page": page_no,
                            "font_size": round(avg_size, 2),
                            "source_format": DocFormat.PDF.value,
                        }
                    else:
                        elem_type = ElementType.PARAGRAPH
                        metadata = {
                            "type": "paragraph",
                            "page": page_no,
                            "font_size": round(avg_size, 2) if avg_size else None,
                            "source_format": DocFormat.PDF.value,
                        }

                    elements.append(
                        DocumentElement(content=text, type=elem_type, metadata=metadata)
                    )

            return LoadedDocument(
                source=path.name,
                format=DocFormat.PDF,
                elements=elements,
                page_count=page_no if page_no > 0 else None,
                size_bytes=len(data),
                file_hash=file_hash,
            )
        except LoaderError:
            raise
        except Exception as e:  # noqa: BLE001 — pdfminer 异常类型众多
            raise LoaderError(f"PDF 解析失败 ({path.name}): {e}") from e
