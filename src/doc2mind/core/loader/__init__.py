"""文档加载器包 — 8 种格式统一接口。

每个 loader 暴露 `extract(path: Path) -> LoadedDocument`。
`detect.py` 按扩展名路由到对应 loader。
"""

from __future__ import annotations

from doc2mind.core.loader.base import Loader, LoaderError, UnsupportedFormatError
from doc2mind.core.loader.detect import detect_format, get_loader

__all__ = [
    "Loader",
    "LoaderError",
    "UnsupportedFormatError",
    "detect_format",
    "get_loader",
]
