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
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doc2mind.core.loader.image_loader import ImageLoader

logger = logging.getLogger("doc2mind.loader.pdf")

from doc2mind.core.loader.base import (
    Loader,
    LoaderError,
    make_source,
    stream_file_hash,
)
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

    def extract(
        self,
        path: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> LoadedDocument:
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
            file_hash, size_bytes = stream_file_hash(path)
            elements: list[DocumentElement] = []
            page_no = 0

            for page_layout in extract_pages(path):
                page_no += 1
                # 页级进度：pdfminer 流式解析拿不到总页数，total 传 0
                # （pipeline 折算为不定进度，仅表示"还在动"）
                if progress is not None:
                    progress(page_no, 0)
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
                elements = _ocr_fallback(path, page_no, progress=progress)

            return LoadedDocument(
                source=make_source(path),
                format=DocFormat.PDF,
                elements=elements,
                page_count=page_no if page_no > 0 else None,
                size_bytes=size_bytes,
                file_hash=file_hash,
            )
        except LoaderError:
            raise
        except Exception as e:  # noqa: BLE001 — pdfminer 异常类型众多
            raise LoaderError(f"PDF 解析失败 ({path.name}): {e}") from e


# OCR 回退渲染 DPI 默认值（可经 DOC2MIND_OCR_RENDER_DPI 覆盖）：
# 越高越准但越慢，200 是精度/速度平衡点
_OCR_RENDER_DPI_DEFAULT = 200
# 分批渲染页数：convert_from_path 一次性渲染整本 PDF 会把所有页的位图
# （200dpi 每页 ~4-8MB）同时压进内存，数百页扫描件直接 OOM；分批渲染 +
# 逐批释放把峰值内存压到常数级。
_OCR_RENDER_BATCH = 8


def _ocr_fallback(
    path: Path,
    page_count: int,
    progress: Callable[[int, int], None] | None = None,
) -> list[DocumentElement]:
    """扫描型 PDF 回退：pdf2image 分批渲染 → PaddleOCR ndarray 直传逐页 OCR。

    使用 pdf2image（基于 poppler）替代 PyMuPDF，规避 AGPL-3.0 传染风险，
    使项目可用于闭源商业分发。

    性能设计：
    - 分批渲染（每批 _OCR_RENDER_BATCH 页）+ 逐批释放，控内存峰值
    - PaddleOCR 无原生 batch 推理（官方确认），CPU 提速走多实例并行
      （DOC2MIND_OCR_WORKERS，每个槽位一个独立 predictor）；GPU 模式
      单卡推理串行更优，自动忽略 worker 数
    - 渲染帧 np.asarray 后直传 OCR：省去逐页 PNG 编码 + 临时文件
      落盘 + 二次读取的往返

    自动探测 poppler 安装位置：配置 poppler_path → 系统 PATH →
    项目 tools/poppler/ → 常见安装目录（见 _find_poppler）。

    Args:
        path: PDF 路径
        page_count: 已知页数
        progress: 页级进度回调 (已完成页数, 总页数)

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

    from doc2mind.core.config import get_settings

    settings = get_settings()
    dpi = max(72, int(getattr(settings, "ocr_render_dpi", 0) or _OCR_RENDER_DPI_DEFAULT))
    workers = max(1, int(getattr(settings, "ocr_workers", 0) or 1))

    # 惰性加载 ImageLoader（触发 PaddleOCR import 检查）
    from doc2mind.core.loader.image_loader import ImageLoader, _detect_ocr_device

    # GPU 模式下多实例无收益（单卡推理串行），并行仅对 CPU 生效
    if _detect_ocr_device() != "cpu":
        workers = 1

    ocr_loader = ImageLoader()
    poppler_path = _find_poppler()

    def _ocr_job(job: tuple[int, object]) -> tuple[int, list[DocumentElement]]:
        """渲染帧 → ndarray → OCR → 带页码元数据的元素（worker 池任务体）。"""
        page_idx, img = job
        import numpy as np

        arr = np.asarray(img)
        raw = ocr_loader.ocr_array(arr, slot=page_idx % workers)
        out: list[DocumentElement] = []
        for el in raw:
            # PDF 无图片占位概念；ocr_array 也不再产生文件名伪标题
            if el.type == ElementType.IMAGE:
                continue
            new_meta = dict(el.metadata)
            new_meta["source_format"] = DocFormat.PDF.value
            new_meta["page"] = page_idx + 1
            new_meta["ocr_extracted"] = True
            out.append(
                DocumentElement(content=el.content, type=el.type, metadata=new_meta)
            )
        return page_idx, out

    elements_by_page: dict[int, list[DocumentElement]] = {}
    done_pages = 0

    def _record(page_idx: int, els: list[DocumentElement]) -> None:
        nonlocal done_pages
        elements_by_page[page_idx] = els
        done_pages += 1
        if progress is not None:
            progress(done_pages, page_count)

    for start in range(1, page_count + 1, _OCR_RENDER_BATCH):
        end = min(start + _OCR_RENDER_BATCH - 1, page_count)
        try:
            images = convert_from_path(
                str(path),
                dpi=dpi,
                poppler_path=poppler_path,
                first_page=start,
                last_page=end,
            )
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
            jobs = [(start + i, img) for i, img in enumerate(images)]
            if workers > 1 and len(jobs) > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    for page_idx, els in pool.map(_ocr_job, jobs):
                        _record(page_idx, els)
            else:
                for page_idx, img in jobs:
                    _record(page_idx, _ocr_job((page_idx, img))[1])
        finally:
            # 显式释放本批 PIL Image 占用的内存
            for img in images:
                with contextlib.suppress(Exception):
                    img.close()

    elements: list[DocumentElement] = []
    for page_idx in sorted(elements_by_page):
        elements.extend(elements_by_page[page_idx])

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
    0. 显式配置 settings.poppler_path（客户端设置页/自动寻找写入，
       经 DOC2MIND_POPPLER_PATH 环境变量注入；配置了必须最优先生效）
    1. 系统 PATH 中的 pdftoppm（已加入 PATH 时直接返回 None）
    2. 项目自带的 tools/poppler/（开发环境）
    3. 常见安装目录
    """
    import shutil

    # 0. 显式配置最高优先（历史缺陷：配置只被状态显示读取，真实渲染从不消费）
    try:
        from doc2mind.core.config import get_settings

        configured = getattr(get_settings(), "poppler_path", "") or ""
        if configured:
            p = Path(configured)
            probe = p / ("pdftoppm.exe" if os.name == "nt" else "pdftoppm")
            if p.is_dir() and probe.is_file():
                return str(p)
            # 配置了但失效：记告警并继续自动探测，而不是静默回退让用户以为配置生效
            logger.warning(
                "配置的 poppler_path=%s 下未找到 pdftoppm，回退自动探测", configured
            )
    except Exception:  # noqa: BLE001 — 配置读取失败不影响自动探测
        pass

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
