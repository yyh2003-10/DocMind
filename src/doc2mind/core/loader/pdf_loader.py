"""PDF 加载器 — 基于 `pdfminer.six`。

特点：
- 纯 Python，无需 Java / PDFMiner C 扩展
- 按 LTTextBox 提取文本块
- 通过字号启发式区分标题 / 正文（avg_size > 16 → heading）
- 表格不直接支持，依赖后续 chunker 做表格保护（连续 | 行视为表格）
- 多栏布局用 `LAParams(detect_vertical=True)`
- **扫描型 PDF 回退**：当 pdfminer 提取 0 元素（纯矢量图纸/扫描图）时，
  用 pdf2image（基于 poppler）把每页渲成图片，调 ImageLoader (PaddleOCR) OCR；
  需系统安装 poppler，OCR 未装则报结构化错误引导用户装 extras。

局限性：
- 复杂表格 / 双栏论文需用 extras 的 opendataloader-pdf
- 图片中的文字需用 image_loader 的 OCR
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doc2mind.core.loader.image_loader import ImageLoader

from doc2mind.core.loader.base import Loader, LoaderError, make_source
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
            # 用 pdf2image 渲每页为图片走 ImageLoader OCR
            if not elements and page_no > 0:
                elements = _ocr_fallback(path, page_no)

            return LoadedDocument(
                source=make_source(path),
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
    """扫描型 PDF 回退：pdf2image 把每页渲成图片 → ImageLoader (PaddleOCR) OCR。

    使用 pdf2image（基于 poppler）替代 PyMuPDF，规避 AGPL-3.0 传染风险，
    使项目可用于闭源商业分发。

    自动探测 poppler 安装位置：
    1. 系统 PATH 中的 pdftoppm（已加入 PATH）
    2. 项目自带的 tools/poppler/（开发环境）
    3. 常见安装目录（C:/tools/poppler、Program Files 等）

    Args:
        path: PDF 路径
        page_count: 已知页数

    Returns:
        OCR 提取的元素列表（按页顺序）

    Raises:
        LoaderError: pdf2image 缺失 / poppler 未装 / OCR 未装 / OCR 失败
    """
    try:
        from pdf2image import convert_from_path
    except ImportError as e:  # pragma: no cover
        raise LoaderError(
            "扫描型 PDF（矢量图纸/扫描图）需 pdf2image 渲染回退，"
            "但 pdf2image 未安装。请运行：pip install pdf2image"
        ) from e

    # 惰性加载 ImageLoader（触发 PaddleOCR import 检查）
    from doc2mind.core.loader.image_loader import ImageLoader

    ocr_loader = ImageLoader()
    elements: list[DocumentElement] = []

    # 自动探测 poppler 路径
    poppler_path = _find_poppler()

    try:
        # pdf2image 返回 PIL Image 列表；需系统安装 poppler（pdfinfo/pdftoppm）
        images = convert_from_path(str(path), dpi=_OCR_RENDER_DPI, poppler_path=poppler_path)
    except Exception as e:  # noqa: BLE001 — pdf2image / poppler 异常类型众多
        msg = str(e).lower()
        if "poppler" in msg or "pdfinfo" in msg or "pdftoppm" in msg:
            raise LoaderError(
                f"pdf2image 渲染 PDF 失败 ({path.name})：未找到 poppler。"
                "请安装 poppler 并将其 bin 目录加入 PATH，"
                "Windows 用户可从 https://github.com/oschwartz10612/poppler-windows 下载。"
            ) from e
        raise LoaderError(f"pdf2image 渲染 PDF 失败 ({path.name}): {e}") from e

    try:
        for page_idx, img in enumerate(images):
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png_bytes = buf.getvalue()
            ocr_elements = _ocr_png_bytes(
                ocr_loader, png_bytes,
                page_no=page_idx + 1,
                pdf_name=path.name,
            )
            elements.extend(ocr_elements)
    finally:
        # 显式释放 PIL Image 占用的内存
        for img in images:
            with contextlib.suppress(Exception):
                img.close()

    if not elements:
        raise LoaderError(
            f"扫描型 PDF OCR 回退失败（{path.name}，{page_count} 页）："
            "PaddleOCR 未识别到任何文字。"
            "请检查图片清晰度，或安装更高级 OCR：pip install 'doc2mind[ocr]'"
        )
    return elements


def _find_poppler() -> str | None:
    """自动探测 poppler 安装位置。

    Returns:
        poppler bin 目录路径，或 None（让 pdf2image 从系统 PATH 查找）

    查找顺序：
    1. 系统 PATH 中的 pdftoppm（已加入 PATH 时直接返回 None）
    2. 项目自带的 tools/poppler/（开发环境）
    3. 常见安装目录
    """
    import shutil

    # 1. 系统 PATH 中的 pdftoppm
    if shutil.which("pdftoppm") is not None:
        return None  # pdf2image 会从 PATH 自动查找

    # 2. 项目自带的 tools/poppler/（相对于当前工作目录或项目根目录）
    #    从 pdf_loader.py 位置向上找到项目根目录（包含 pyproject.toml）
    current = Path(__file__).resolve().parent
    project_root = None
    for _ in range(10):  # 最多向上查 10 层
        if (current / "pyproject.toml").exists():
            project_root = current
            break
        parent = current.parent
        if parent == current:  # 到达根目录
            break
        current = parent

    # 如果找不到 pyproject.toml，用当前工作目录
    if project_root is None:
        project_root = Path.cwd()

    poppler_candidates = [
        project_root / "tools" / "poppler",
    ]

    # 递归搜索 poppler 子目录中的 bin/pdftoppm.exe
    for base in poppler_candidates:
        if not base.exists():
            continue
        for sub in sorted(base.iterdir(), reverse=True):  # 优先选版本高的
            bin_dir = sub / "Library" / "bin"
            if (bin_dir / "pdftoppm.exe").exists():
                return str(bin_dir)
            bin_dir = sub / "bin"
            if (bin_dir / "pdftoppm.exe").exists():
                return str(bin_dir)

    # 3. 常见安装目录
    common_paths = [
        Path("C:/tools/poppler"),
        Path("C:/Program Files/poppler"),
        Path("C:/Program Files (x86)/poppler"),
        Path.home() / "poppler",
    ]
    for base in common_paths:
        if not base.exists():
            continue
        for sub in sorted(base.iterdir(), reverse=True):
            bin_dir = sub / "Library" / "bin"
            if (bin_dir / "pdftoppm.exe").exists():
                return str(bin_dir)
            bin_dir = sub / "bin"
            if (bin_dir / "pdftoppm.exe").exists():
                return str(bin_dir)

    return None  # 未找到，让 pdf2image 从 PATH 查找并报错


def _ocr_png_bytes(
    ocr_loader: ImageLoader,  # noqa: F821
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
        with contextlib.suppress(OSError):
            tmp_path.unlink()
