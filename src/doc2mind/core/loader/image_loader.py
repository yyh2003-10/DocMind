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
import os
from pathlib import Path
from typing import Any

from doc2mind.core.loader.base import Loader, LoaderError
from doc2mind.core.models import (
    DocFormat,
    DocumentElement,
    ElementType,
    LoadedDocument,
)


# 单例 OCR 实例缓存（key: lang）
# key: (lang, device) → PaddleOCR 实例；同一语言 GPU/CPU 各缓存一份，
# 便于 GPU 推理失败时回退 CPU 复用实例
_OCR_INSTANCES: dict[tuple[str, str], object] = {}
# 运行时 GPU 推理失败后置 True：之后一律走 CPU，避免每次重复 GPU 崩溃
_OCR_GPU_INFERENCE_BROKEN = False


def _disable_paddle_pir() -> None:
    """禁用 PaddlePaddle PIR 新执行器与 oneDNN（在 import paddle 之前设置环境变量）。

    背景：Paddle 3.x 默认开启 PIR 执行器，且部分算子会走 oneDNN 指令；
    在 OCR 推理时会抛
    ``(Unimplemented) ConvertPirAttribute2RuntimeAttribute not support
    [pir::ArrayAttribute<pir::DoubleAttribute>] (onednn_instruction.cc:118)``
    导致扫描型 PDF / 图片 OCR 100% 失败（HTTP 500 / ingest failed）。

    已知 workaround 是回退旧执行器并关闭 oneDNN。这里只设环境变量——
    paddle 在 import paddleocr 时才首次初始化，环境变量恰好在此之前生效；
    不要显式 import paddle + set_flags：先 import paddle 再 import paddleocr
    （内部会 import torch）会触发 torch shm.dll 加载冲突（WinError 127）。
    """
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")


def _register_nvidia_dll_dirs() -> None:
    """Windows 下把 pip 安装的 nvidia GPU 运行时（cu12/cu13）bin 目录
    注册进 DLL 搜索路径，必须在 import paddle / paddleocr 之前调用。

    背景：paddlepaddle-gpu 3.x 通过 pip 依赖 nvidia-cudnn-cu12、nvidia-cublas-cu12、
    nvidia-cusolver-cu12 等包，DLL 位于 ``site-packages/nvidia/<pkg>/bin``。
    Windows 的 LoadLibrary 默认不搜这些目录，直接 import paddle 会报
    ``WinError 126/127``（找不到 cublas64_12.dll / cusolver64_11.dll 等）。
    不注册时 `_detect_ocr_device()` 只能回退 "cpu"，GPU 加速完全失效。

    实现已抽到 `doc2mind.core.nvidia_runtime`，与 fastembed 嵌入器共用。
    """
    from doc2mind.core.nvidia_runtime import register_nvidia_dll_dirs

    register_nvidia_dll_dirs()


def _detect_ocr_device() -> str:
    """检测 OCR 推理设备：可用 NVIDIA GPU 则返回 'gpu:0'，否则 'cpu'。

    paddleocr 的导入链内部已加载 paddle，此处 `import paddle` 只是拿缓存
    模块做能力检测，不会触发与 torch 的 DLL 加载顺序冲突。
    """
    # Windows：先注册 nvidia 运行时 DLL 目录，否则 import paddle 报 WinError
    _register_nvidia_dll_dirs()
    try:
        import paddle  # noqa: PLC0415

        if (
            paddle.device.is_compiled_with_cuda()
            and paddle.device.cuda.device_count() > 0
        ):
            return "gpu:0"
    except Exception:  # noqa: BLE001 — 检测失败一律回退 CPU
        pass
    return "cpu"


def _get_ocr(lang: str = "ch", device: str | None = None) -> object:
    """惰性加载并缓存 PaddleOCR 实例（按语言 + 设备缓存）。

    Args:
        lang: OCR 语言代码，'ch' 中英混合 / 'en' 纯英文 / 'japan' 等
        device: 推理设备；None 自动检测（GPU 可用则 gpu:0，否则 cpu）

    Returns:
        PaddleOCR 实例

    Raises:
        LoaderError: PaddleOCR 未安装或加载失败
    """
    # Windows：先注册 nvidia 运行时 DLL 目录，否则 import paddleocr 报 WinError
    _register_nvidia_dll_dirs()
    # 必须先于 import paddleocr 执行（paddle 一旦初始化，flags 不再生效）
    _disable_paddle_pir()

    try:
        from paddleocr import PaddleOCR
    except ImportError as e:
        raise LoaderError(
            "PaddleOCR 未安装。请运行：pip install 'doc2mind[ocr]'"
        ) from e

    # 设备检测必须放在 paddleocr import 之后：
    # paddleocr 导入链内部已 import paddle，此处再 import paddle 只是缓存命中，
    # 不会像"先 import paddle 再 import paddleocr"那样触发 torch shm.dll
    # 加载冲突（WinError 127，见 _disable_paddle_pir 注释）。
    if device is None:
        device = _detect_ocr_device()
        # GPU 推理已确认崩溃（见 extract 的运行时回退）→ 直接走 CPU
        if _OCR_GPU_INFERENCE_BROKEN and device != "cpu":
            device = "cpu"
    key = (lang, device)
    if key in _OCR_INSTANCES:
        return _OCR_INSTANCES[key]

    try:
        # PaddleOCR 3.7+：use_angle_cls 已废弃，改 use_textline_orientation
        import inspect

        sig = inspect.signature(PaddleOCR.__init__)
        init_kwargs = {"lang": lang}
        # 优先用新参数名，旧版回退到 use_angle_cls
        if "use_textline_orientation" in sig.parameters:
            init_kwargs["use_textline_orientation"] = True
        elif "use_angle_cls" in sig.parameters:
            init_kwargs["use_angle_cls"] = True
        # show_log 仅旧版支持
        if "show_log" in sig.parameters:
            init_kwargs["show_log"] = False
        # 关键修复：PaddleOCR 3.7 默认 enable_mkldnn=True（_constants.DEFAULT_ENABLE_MKLDNN），
        # 在 Paddle 3.x PIR 执行器下触发 oneDNN 指令崩溃
        # （ConvertPirAttribute2RuntimeAttribute not support，HTTP 500）。
        # 必须显式关闭 oneDNN —— 环境变量 FLAGS_use_mkldnn=0 无法覆盖 predictor 层设置。
        init_kwargs["enable_mkldnn"] = False

        init_kwargs["device"] = device

        try:
            ocr = PaddleOCR(**init_kwargs)
        except Exception as gpu_err:  # noqa: BLE001
            if device != "cpu":
                # GPU 初始化失败（驱动/显存/模型不兼容）→ 回退 CPU 重试
                init_kwargs["device"] = "cpu"
                ocr = PaddleOCR(**init_kwargs)
                # CPU 实例单独缓存，供 extract 运行时回退复用
                _OCR_INSTANCES[(lang, "cpu")] = ocr
            else:
                raise gpu_err
    except Exception as e:  # noqa: BLE001 — PaddleOCR 初始化失败原因多样
        raise LoaderError(
            f"PaddleOCR 初始化失败：{e}。首次运行需下载模型，请检查网络。"
        ) from e

    _OCR_INSTANCES[key] = ocr
    return ocr


def _extract_region_text(result) -> list[tuple[str, list[list[float]]]]:
    """从 PaddleOCR 原始输出中提取 (text, bbox) 列表。

    兼容两种输出结构：
    - PaddleOCR 3.x（paddlex OCRResult，dict 子类）：
        {'rec_texts': [...], 'rec_scores': [...], 'rec_polys': [ndarray(4,2), ...]}
    - PaddleOCR 2.x：
        [ [bbox_4points, (text, confidence)], ... ]
    若某图片无识别结果，对应位置为 None。

    Args:
        result: PaddleOCR 返回值的第一层（OCRResult 或 list）

    Returns:
        [(text, bbox), ...] 其中 bbox 为 [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]
    """
    out: list[tuple[str, list[list[float]]]] = []
    if not result:
        return out

    # PaddleOCR 3.x：paddlex OCRResult 是 dict 子类，rec_texts/rec_polys 并列存放
    if isinstance(result, dict) and result.get("rec_texts"):
        texts = result["rec_texts"]
        polys = result.get("rec_polys") or result.get("dt_polys") or []
        for i, text in enumerate(texts):
            if not text:
                continue
            bbox_float: list[list[float]] = []
            if i < len(polys) and polys[i] is not None:
                try:
                    bbox_float = [[float(p[0]), float(p[1])] for p in polys[i]]
                except (TypeError, IndexError, ValueError):
                    bbox_float = []
            out.append((text, bbox_float))
        return out

    # PaddleOCR 2.x：list 格式
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
            # PaddleOCR 3.7+：ocr() 的 cls 参数已废弃（初始化时用_textline_orientation=True替代）
            import inspect

            ocr_kwargs: dict[str, Any] = {}
            sig = inspect.signature(ocr.ocr)
            if "cls" in sig.parameters:
                ocr_kwargs["cls"] = True  # 旧版兼容
            try:
                result = ocr.ocr(str_path, **ocr_kwargs)
            except Exception as gpu_err:  # noqa: BLE001
                # 运行时 GPU 推理崩溃（CUDNN 版本不匹配 / 显存不足，见 _detect_ocr_device
                # 与 _disable_paddle_pir 注释）→ 回退 CPU 实例重试一次，
                # 并把 _OCR_GPU_INFERENCE_BROKEN 置位，后续请求直接走 CPU。
                global _OCR_GPU_INFERENCE_BROKEN
                _OCR_GPU_INFERENCE_BROKEN = True
                cpu_ocr = _get_ocr(self.lang, device="cpu")
                result = cpu_ocr.ocr(str_path, **ocr_kwargs)

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
