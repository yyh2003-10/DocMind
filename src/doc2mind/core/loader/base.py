"""加载器抽象接口与异常类型。"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from doc2mind.core.models import LoadedDocument


class LoaderError(Exception):
    """加载器基类异常。"""


class UnsupportedFormatError(LoaderError):
    """不支持的文件格式。"""


def stream_file_hash(path: Path, chunk_size: int = 1 << 20) -> tuple[str, int]:
    """流式计算文件 MD5 与字节数（1MB 分块读，不把整个文件压进内存）。

    各 loader 历史上用 `read_bytes()` 全量读入再 md5——几百 MB 的 PDF
    直接翻倍内存占用。与全量 md5 结果逐字节一致，可安全用于去重比对。
    """
    h = hashlib.md5()
    size = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
            size += len(block)
    return h.hexdigest(), size


def make_source(path: Path) -> str:
    """生成文档的稳定 source 标识：解析后的绝对路径。

    库内文档以 `UNIQUE(collection, source)` 做替换语义（重新导入同文件时
    先删旧再写新）。历史上 source 只取文件名，导致不同目录的同名文件
    （如 A/readme.md 与 B/readme.md）互相覆盖、旧内容静默丢失 —— 因此
    改为包含完整路径。相对路径导入时 resolve() 保证同一文件得到同一 source。
    """
    try:
        return str(path.resolve())
    except OSError:  # 路径不可解析（如已删除）时退回 absolute()
        return str(path.absolute())


class Loader(ABC):
    """加载器抽象基类。

    子类必须实现 `extract`，返回 `LoadedDocument`。
    构造时接收 `Settings` 用于读取分块/嵌入相关参数（多数 loader 不需要）。
    """

    #: 该 loader 支持的扩展名（小写，无前导点），子类覆盖。
    supported_extensions: tuple[str, ...] = ()

    @abstractmethod
    def extract(
        self,
        path: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> LoadedDocument:
        """解析文档，返回 `LoadedDocument`。

        Args:
            path: 文件路径
            progress: 可选的文件内进度回调 `(done, total)`。多页文档（PDF
                逐页解析 / 扫描件逐页 OCR）按页上报，供 pipeline 折算成
                parsing 阶段进度；total 未知（如流式解析到哪算哪）时传 0。
                慢速 loader 应尽量支持，快速 loader 可忽略。

        Returns:
            `LoadedDocument`，其中 `elements` 按文档顺序排列。

        Raises:
            LoaderError: 文件损坏、解析失败
            UnsupportedFormatError: 扩展名不在支持列表
        """
        raise NotImplementedError

    def matches(self, path: Path) -> bool:
        """判断该 loader 是否支持给定路径（按扩展名）。"""
        return path.suffix.lower().lstrip(".") in self.supported_extensions
