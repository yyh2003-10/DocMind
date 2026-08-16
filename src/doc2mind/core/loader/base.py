"""加载器抽象接口与异常类型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from doc2mind.core.models import LoadedDocument


class LoaderError(Exception):
    """加载器基类异常。"""


class UnsupportedFormatError(LoaderError):
    """不支持的文件格式。"""


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
    def extract(self, path: Path) -> LoadedDocument:
        """解析文档，返回 `LoadedDocument`。

        Args:
            path: 文件路径

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
