"""PDF 加载器 — 基于 `pdfminer.six`。

特点：
- 纯 Python，无需 Java / PDFMiner C 扩展
- 按 LTTextBox 提取文本块
- 通过字号启发式区分标题 / 正文（avg_size > 16 → heading）
- 表格不直接支持，依赖后续 chunker 做表格保护（连续 | 行视为表格）
- 多栏布局用 `LAParams(detect_vertical=True)`
- **扫描型 PDF 回退**：当 pdfminer 提取 0 元素（纯矢量图纸/扫描图）时，
  用 PyMuPDF (fitz) 把每页渲成图片，调 ImageLoader (PaddleOCR) OCR；
  OCR 未装则报结构化错误引导用户装 extras。

局限性：
- 复杂表格 / 双栏论文需用 extras 的 opendataloader-pdf
- 图片中的文字需用 image_loader 的 OCR
"""

from __future__ import annotations

import hashlib
import tempfile
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

            # 扫描型 PDF 回退：pdfminer 提 0 元素 → 矢量图纸/扫描图，
            # 用 PyMuPDF 渲每页为图片走 ImageLoader OCR
            if not elements and page_no > 0:
                elements = _ocr_fallback(path, page_no)

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


# OCR 回退渲染 DPI（越高精度越好但越慢，200 是精度/速度平衡点）
_OCR_RENDER_DPI = 200


def _ocr_fallback(path: Path, page_count: int) -> list[DocumentElement]:
    """扫描型 PDF 回退：PyMuPDF 渲每页为图片 → ImageLoader (PaddleOCR) OCR。

    Args:
        path: PDF 路径
        page_count: 已知页数

    Returns:
        OCR 提取的元素列表（按页顺序）

    Raises:
        LoaderError: PyMuPDF 缺失 / OCR 未装 / OCR 失败
    """
    try:
        import fitz  # PyMuPDF（已装，非 extras 依赖）
    except ImportError as e:  # pragma: no cover
        raise LoaderError(
            "扫描型 PDF（矢量图纸/扫描图）需 PyMuPDF 渲染回退，"
            "但 PyMuPDF 未安装。请运行：pip install PyMuPDF"
        ) from e

    # 惰性加载 ImageLoader（触发 PaddleOCR import 检查）
    from doc2mind.core.loader.image_loader import ImageLoader

    ocr_loader = ImageLoader()
    elements: list[DocumentElement] = []

    try:
        doc = fitz.open(str(path))
    except Exception as e:  # noqa: BLE001
        raise LoaderError(f"PyMuPDF 打开 PDF 失败 ({path.name}): {e}") from e

    try:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            pix = page.get_pixmap(dpi=_OCR_RENDER_DPI)
            png_bytes = pix.tobytes("png")
            ocr_elements = _ocr_png_bytes(
                ocr_loader, png_bytes,
                page_no=page_idx + 1,
                pdf_name=path.name,
            )
            elements.extend(ocr_elements)
    finally:
        doc.close()

    if not elements:
        raise LoaderError(
            f"扫描型 PDF OCR 回退失败（{path.name}，{page_count} 页）："
            "PaddleOCR 未识别到任何文字。"
            "请检查图片清晰度，或安装更高级 OCR：pip install 'doc2mind[ocr]'"
        )
    return elements


def _ocr_png_bytes(
    ocr_loader: ImageLoader,
    png_bytes: bytes,
    page_no: int,
    pdf_name: str,
) -> list[DocumentElement]:
    """对 PNG bytes 跑 OCR，返回带页码元数据的元素列表。

    ImageLoader.extract 接收文件路径，这里把 bytes 写临时文件再调，
    避免改 ImageLoader 内部逻辑（它硬依赖 PaddleOCR 接路径）。
    """
    # 临时文件名带页码，便于 OCR 元数据追溯
    with tempfile.NamedTemporaryFile(
        suffix=f"_p{page_no}.png", delete=False
    ) as tmp:
        tmp.write(png_bytes)
        tmp_path = Path(tmp.name)

    try:
        loaded = ocr_loader.extract(tmp_path)
        # 改写元数据：把 source_format 标回 PDF，加 page 字段
        out: list[DocumentElement] = []
        for el in loaded.elements:
            new_meta = dict(el.metadata)
            new_meta["source_format"] = DocFormat.PDF.value
            new_meta["page"] = page_no
            new_meta["ocr_extracted"] = True
            # 文件名占位的 IMAGE 元素跳过（PDF 无图片概念）
            if el.type == ElementType.IMAGE:
                continue
            # ImageLoader 会把 path.stem 合成 `# <stem>` 的 H1 标题，
            # 而这里是随机临时文件名（如 tmpabc123_p1），必须跳过，
            # 否则每页都会向知识库注入随机伪标题、污染分块与检索结果。
            if el.type == ElementType.HEADING and el.content == f"# {tmp_path.stem}":
                continue
            out.append(
                DocumentElement(content=el.content, type=el.type, metadata=new_meta)
            )
        return out
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
