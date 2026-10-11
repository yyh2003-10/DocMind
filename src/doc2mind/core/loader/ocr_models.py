"""OCR 官方模型目录 / 国内源 / 缓存检测与下载。

背景：PaddleOCR 3.x 经 PaddleX 在首次推理前下载检测/识别/方向模型。
默认源 HuggingFace 在国内常不可用，且缓存目录固定为 ``~/.paddlex``，
用户无法切换。本模块：

1. **统一模型目录**：``settings.ocr_model_dir``（默认
   ``%LOCALAPPDATA%/doc2mind/paddle_models``），经
   ``PADDLE_PDX_CACHE_HOME`` 注入 PaddleX，模型落在
   ``<ocr_model_dir>/official_models/``。
2. **国内源优先**：``PADDLE_PDX_MODEL_SOURCE`` 默认 ``modelscope``
   （可配 ``bos``），并关闭 1s 连通性探测；HF 兜底走 hf-mirror。
3. **缓存检测 + 显式下载**：提供 :func:`ocr_models_cached` /
   :func:`download_ocr_models`，供安装后预下载与设置页「下载 OCR 模型」。

**必须在 import paddleocr / paddlex 之前调用**
:func:`configure_paddle_model_env`——PaddleX 在 import 时读取
``PADDLE_PDX_CACHE_HOME`` / ``PADDLE_PDX_MODEL_SOURCE`` 到模块级常量。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("doc2mind.loader.ocr_models")

# 国内模型源别名（PaddleX ModelHoster.alias）：modelscope / bos / aistudio
_DEFAULT_MODEL_SOURCE = "modelscope"
_HF_MIRROR = "https://hf-mirror.com"

# 中文 + 文本行方向分类时 PaddleOCR 默认会拉取的官方模型名
# （见 paddlex configs/pipelines/OCR.yaml；关闭 doc 预处理后不含 doc_ori/UVDoc）
_CH_MODELS: tuple[str, ...] = (
    "PP-OCRv6_medium_det",
    "PP-OCRv6_medium_rec",
    "PP-LCNet_x1_0_textline_ori",
)
_EN_MODELS: tuple[str, ...] = (
    "PP-OCRv5_server_det",
    "en_PP-OCRv5_mobile_rec",
    "PP-LCNet_x1_0_textline_ori",
)

_env_configured = False


def get_ocr_model_dir() -> Path:
    """OCR/PaddleX 模型缓存根目录（含 official_models/ 子目录）。"""
    from doc2mind.core.config import get_settings

    raw = getattr(get_settings(), "ocr_model_dir", None)
    if raw:
        return Path(raw).expanduser()
    # 兜底与 config 默认一致，避免未初始化 settings 时飘到 ~/.paddlex
    from doc2mind.core.config import _user_data_dir

    return _user_data_dir() / "paddle_models"


def ocr_model_source() -> str:
    """模型下载源优先级：modelscope（默认）/ bos / aistudio / huggingface。"""
    from doc2mind.core.config import get_settings

    raw = (getattr(get_settings(), "ocr_model_source", "") or "").strip().lower()
    return raw or _DEFAULT_MODEL_SOURCE


def required_ocr_models(lang: str = "ch") -> tuple[str, ...]:
    """当前语言配置下 PaddleOCR 需要的官方模型名。"""
    if lang.lower() in ("en", "english"):
        return _EN_MODELS
    return _CH_MODELS


def _official_models_dir() -> Path:
    return get_ocr_model_dir() / "official_models"


def configure_paddle_model_env() -> Path:
    """在 import paddleocr/paddlex 之前注入模型目录与国内源。

    Returns:
        生效的模型缓存根目录
    """
    global _env_configured
    model_dir = get_ocr_model_dir()
    cache_home = str(model_dir)
    # PaddleX 仅在 import 时读取这些变量；已配置过且未变则跳过
    os.environ["PADDLE_PDX_CACHE_HOME"] = cache_home
    source = ocr_model_source()
    os.environ["PADDLE_PDX_MODEL_SOURCE"] = source
    # 1s 连通性探测在国内网络下会误杀可用源；关闭后按 MODEL_SOURCE 优先回退
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    # HF 兜底端点（仅在仍会尝试 huggingface 时生效）
    os.environ.setdefault("PADDLE_PDX_HUGGING_FACE_ENDPOINT", _HF_MIRROR)
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("无法创建 OCR 模型目录 %s: %s", model_dir, e)
    if not _env_configured:
        logger.info(
            "PaddleOCR 模型目录=%s 源=%s", cache_home, source
        )
        _env_configured = True
    return model_dir


def ocr_models_cached(lang: str = "ch") -> bool:
    """所需 OCR 模型是否已在本地缓存（目录存在且非空）。"""
    root = _official_models_dir()
    for name in required_ocr_models(lang):
        d = root / name
        if not d.is_dir():
            return False
        # 半截下载：目录存在但没有权重文件
        if not any(d.rglob("*")):
            return False
        if not any(
            p.suffix in {".pdiparams", ".json", ".yml", ".yaml", ".onnx", ".pdmodel"}
            or p.name in {"inference.pdiparams", "inference.yml", "config.json"}
            for p in d.rglob("*")
            if p.is_file()
        ):
            return False
    return True


def ocr_model_cache_status(lang: str = "ch") -> dict:
    """供设置页/环境自检展示的缓存状态。"""
    model_dir = get_ocr_model_dir()
    names = required_ocr_models(lang)
    root = _official_models_dir()
    missing = [n for n in names if not (root / n).is_dir()]
    return {
        "ocr_model_dir": str(model_dir),
        "ocr_model_source": ocr_model_source(),
        "ocr_model_cached": not missing,
        "ocr_models": list(names),
        "ocr_models_missing": missing,
        "official_models_dir": str(root),
    }


def download_ocr_models(
    lang: str = "ch",
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> list[str]:
    """下载当前语言所需 OCR 模型到配置目录（幂等：已有缓存则跳过）。

    Args:
        lang: OCR 语言（决定 det/rec 模型名）
        progress_cb: ``(已完成数, 总数, 当前模型名)`` 进度回调

    Returns:
        实际下载/就绪的模型本地目录列表

    Raises:
        RuntimeError: 全部源下载失败
    """
    configure_paddle_model_env()
    names = required_ocr_models(lang)
    root = _official_models_dir()
    root.mkdir(parents=True, exist_ok=True)

    # 延迟 import：确保环境变量已注入
    from paddlex.inference.utils.official_models import _ModelManager

    manager = _ModelManager()
    # 强制重建 hoster 列表（若进程内曾以旧源初始化过）
    manager._hosters = None

    results: list[str] = []
    errors: list[str] = []
    total = len(names)
    for i, name in enumerate(names):
        target = root / name
        if target.is_dir() and any(target.rglob("*")):
            logger.info("OCR 模型已缓存，跳过下载: %s", name)
            results.append(str(target))
            if progress_cb:
                progress_cb(i + 1, total, name)
            continue
        try:
            path = manager.get_model_path(name)
            results.append(str(path))
            if progress_cb:
                progress_cb(i + 1, total, name)
        except Exception as e:  # noqa: BLE001 — PaddleX 异常类型多样
            errors.append(f"{name}: {e}")
            logger.warning("OCR 模型下载失败 %s: %s", name, e)
            # 半截目录会挡住重试，删掉让下次重下
            if target.exists() and not any(target.rglob("*")):
                try:
                    target.rmdir()
                except OSError:
                    pass

    if errors and not results:
        raise RuntimeError(
            "OCR 模型下载失败（国内源 modelscope/bos）：\n  "
            + "\n  ".join(errors)
            + f"\n模型目录：{get_ocr_model_dir()}\n"
            "请检查网络后在设置页重试「下载 OCR 模型」，"
            "或手动将模型放入该目录下的 official_models/。"
        )
    return results


def ocr_download_hint(lang: str = "ch") -> str:
    """模型未缓存时给用户的操作提示（空字符串 = 已就绪）。"""
    if ocr_models_cached(lang):
        return ""
    status = ocr_model_cache_status(lang)
    missing = "、".join(status["ocr_models_missing"]) or "、".join(status["ocr_models"])
    return (
        f"OCR 识别模型尚未下载（{missing}，约 150MB）。\n"
        f"将下载到：{status['ocr_model_dir']}\n"
        f"下载源：{status['ocr_model_source']}（国内）。\n"
        "请在「设置 → 外部组件路径 / OCR」点击「下载 OCR 模型」，"
        "或重新安装 OCR 组件时选择立即下载。"
    )
