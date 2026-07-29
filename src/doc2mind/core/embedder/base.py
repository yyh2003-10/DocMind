"""嵌入引擎抽象接口与异常。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Sequence

from doc2mind.core.chunker.base import Chunk


class EmbedderError(Exception):
    """嵌入引擎异常。"""


class Embedder(ABC):
    """嵌入引擎抽象基类。

    子类必须实现：
        - `embed(chunks) -> Iterator[ndarray]`
        - `embed_query(text) -> ndarray`
        - `dimension` 属性
        - `model_name` 属性

    约定：
    - 输出向量为 `np.ndarray`，dtype 通常 float32
    - 单条查询走 `embed_query`（不带 normalize 之外的复杂处理）
    - 批量嵌入走 `embed`，逐批 yield
    """

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度。"""
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名（用于日志、metadata）。"""
        raise NotImplementedError

    @abstractmethod
    def embed(self, chunks: Sequence[Chunk]) -> Iterator:
        """批量嵌入。

        Args:
            chunks: `Chunk` 序列

        Yields:
            `np.ndarray` —— 与输入顺序一一对应的向量
        """
        raise NotImplementedError

    @abstractmethod
    def embed_query(self, text: str) -> "object":
        """嵌入单条查询文本。

        Args:
            text: 查询文本

        Returns:
            `np.ndarray` 向量
        """
        raise NotImplementedError

    def embed_texts(self, texts: Sequence[str]) -> Iterator:
        """嵌入纯文本列表（不走 Chunk 路径，重建索引用）。

        默认实现把每个 text 包成临时 Chunk。
        """
        from doc2mind.core.chunker.base import Chunk

        fake_chunks = [Chunk(content=t, tokens=0) for t in texts]
        yield from self.embed(fake_chunks)

    def embed_text(self, text: str) -> "object":
        """嵌入单条文本（与 `embed_query` 等价，命名更直观）。"""
        return self.embed_query(text)
