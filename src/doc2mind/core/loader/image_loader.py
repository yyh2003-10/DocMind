"""图片 OCR 加载器 — 基于 `PaddleOCR` (extras)。

特点：
- 仅对图片调用 PaddleOCR（绝不调用 `ocr.ocr(pdf_path, type='pdf')`，会触发 PyMuPDF AGPL）
- 单例化 OCR 实例，避免重复加载模型（首次 ~2-3s）
- 中英文双语支持：默认 `lang='ch'`，可在 Settings 配置
- 输出按检测区域分组的 paragraph 元素，附带 bbox 元数据
- 文件名当作 H1 标题，方便后续 chunker 切分

局限性：
- 首次运行需下载 PaddleOCR 检测/识别模型 (~50MB)
- 手写体 / 复杂表格识别效果有限
- 大图（>4K）需先缩放，否则 OOM

安全合规：
- PaddleOCR 本身 Apache 2.0
- 但 PaddleOCR 内部 PDF 调用走 PyMuPDF (AGPL-3.0)，传染性强
- 本 loader 严格只处理图片扩展名，detect.py 已限制
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


# 单例 OCR 实例缓存（key: lang）
_OCR_INSTANCES: dict[str, object] = {}


def _get_ocr(lang: str = "ch") -> object:
    """惰性加载并缓存 PaddleOCR 实例。

    Args:
        lang: OCR 语言代码，'ch' 中英混合 / 'en' 纯英文 / 'japan' 等

    Returns:
        PaddleOCR 实例

    Raises:
        LoaderError: PaddleOCR 未安装或加载失败
    """
    if lang in _OCR_INSTANCES:
        return _OCR_INSTANCES[lang]

    try:
        from paddleocr import PaddleOCR
    except ImportError as e:
        raise LoaderError(
            "PaddleOCR 未安装。请运行：pip install 'doc2mind[ocr]'"
        ) from e

    try:
        # use_angle_cls=True 识别旋转文字
        # show_log=False 避免污染 stderr
        ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    except Exception as e:  # noqa: BLE001 — PaddleOCR 初始化失败原因多样
        raise LoaderError(
            f"PaddleOCR 初始化失败：{e}。首次运行需下载模型，请检查网络。"
        ) from e

    _OCR_INSTANCES[lang] = ocr
    return ocr


def _extract_region_text(result: list) -> list[tuple[str, list[list[float]]]]:
    """从 PaddleOCR 原始输出中提取 (text, bbox) 列表。

    PaddleOCR 输出结构（v2.x）：
        [
            [ [bbox_4points], (text, confidence) ],
            ...
        ]
    若某图片无识别结果，对应位置为 None。

    Args:
        result: PaddleOCR 返回值的第一层 list

    Returns:
        [(text, bbox), ...] 其中 bbox 为 [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]
    """
    out: list[tuple[str, list[list[float]]]] = []
    if not result:
        return out
    for line in result:
        if line is None:
            continue
        # 兼容两种返回格式
        if len(line) == 2 and isinstance(line[1], tuple):
            bbox, (text, _conf) = line
        elif len(line) == 2 and isinstance(line[1], list):
            bbox, info = line
            text = info[0] if info else ""
        else:
            continue
        if not text:
            continue
        # bbox 是 4 个 [x, y] 点
        try:
            bbox_float = [[float(p[0]), float(p[1])] for p in bbox]
        except (TypeError, IndexError, ValueError):
            bbox_float = []
        out.append((text, bbox_float))
    return out


def _region_to_paragraph(
    regions: list[tuple[str, list[list[float]]]],
) -> list[DocumentElement]:
    """把识别区域按 y 坐标排序，相邻行合并为段落。

    合并启发式：
    - 按 bbox 中心 y 升序排序（图片坐标系 y 向下）
    - 若两行 y 差 < 行高的 0.6 倍，视为同一段落
    """
    if not regions:
        return []

    # 计算每行的 (center_y, height, text, bbox)
    rows: list[tuple[float, float, str, list[list[float]]]] = []
    for text, bbox in regions:
        if not bbox or len(bbox) < 4:
            ys = [0.0]
            xs = [0.0]
        else:
            ys = [p[1] for p in bbox]
            xs = [p[0] for p in bbox]
        center_y = sum(ys) / len(ys)
        height = max(ys) - min(ys) if len(ys) > 1 else 0
        rows.append((center_y, height, text, bbox))

    # 按 y 升序
    rows.sort(key=lambda r: r[0])

    elements: list[DocumentElement] = []
    current_lines: list[str] = []
    current_bbox: list[list[float]] = []
    last_y: float | None = None
    last_height: float = 0.0

    for center_y, height, text, bbox in rows:
        if last_y is not None:
            # 同段落判定：y 差小于上一行高度的 0.6 倍
            gap = abs(center_y - last_y)
            if gap > last_height * 0.6 + 5:
                # 段落边界，flush
                if current_lines:
                    elements.append(
                        DocumentElement(
                            content="\n".join(current_lines),
                            type=ElementType.PARAGRAPH,
                            metadata={
                                "type": "paragraph",
                                "bbox": current_bbox,
                                "source_format": DocFormat.IMAGE.value,
                            },
                        )
                    )
                    current_lines = []
                    current_bbox = []
        current_lines.append(text)
        current_bbox.extend(bbox)
        last_y = center_y
        last_height = height if height > 0 else last_height

    # flush 最后一段
    if current_lines:
        elements.append(
            DocumentElement(
                content="\n".join(current_lines),
                type=ElementType.PARAGRAPH,
                metadata={
                    "type": "paragraph",
                    "bbox": current_bbox,
                    "source_format": DocFormat.IMAGE.value,
                },
            )
        )

    return elements


class ImageLoader(Loader):
    """图片 OCR 加载器（PaddleOCR 实现）。

    Args:
        lang: OCR 语言，默认 'ch'（中英混合）
    """

    supported_extensions = (
        "png",
        "jpg",
        "jpeg",
        "bmp",
        "webp",
        "tif",
        "tiff",
        "gif",
    )

    def __init__(self, lang: str = "ch") -> None:
        self.lang = lang

    def extract(self, path: Path) -> LoadedDocument:
        if not path.exists():
            raise LoaderError(f"文件不存在: {path}")

        try:
            data = path.read_bytes()
            file_hash = hashlib.md5(data).hexdigest()
            ocr = _get_ocr(self.lang)

            # PaddleOCR 接收字符串路径
            str_path = str(path)
            # 注意：绝不调用 ocr.ocr(pdf, type='pdf')，会触发 PyMuPDF AGPL
            result = ocr.ocr(str_path, cls=True)

            # result 形如 [ [line1, line2, ...] ]，取第一层
            if result and isinstance(result, list) and len(result) > 0:
                regions = _extract_region_text(result[0])
            else:
                regions = []

            elements: list[DocumentElement] = []

            # 文件名当 H1，便于 chunker 切分
            elements.append(
                DocumentElement(
                    content=f"# {path.stem}",
                    type=ElementType.HEADING,
                    metadata={
                        "type": "heading",
                        "level": 1,
                        "source_format": DocFormat.IMAGE.value,
                    },
                )
            )

            # IMAGE 类型元素占位，便于后续 chunker 区分
            elements.append(
                DocumentElement(
                    content="",
                    type=ElementType.IMAGE,
                    metadata={
                        "type": "image",
                        "filename": path.name,
                        "size_bytes": len(data),
                        "source_format": DocFormat.IMAGE.value,
                    },
                )
            )

            elements.extend(_region_to_paragraph(regions))

            return LoadedDocument(
                source=path.name,
                format=DocFormat.IMAGE,
                elements=elements,
                page_count=1,
                size_bytes=len(data),
                file_hash=file_hash,
            )
        except LoaderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LoaderError(f"图片 OCR 失败 ({path.name}): {e}") from e
