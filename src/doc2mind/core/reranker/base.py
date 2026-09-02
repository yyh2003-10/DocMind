"""重排器抽象接口与异常。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class RerankerError(Exception):
    """重排器异常（加载失败 / 推理失败）。"""


class Reranker(ABC):
    """重排器抽象基类。

    子类必须实现：
        - `rerank(query, documents) -> list[float]`

    约定：
        - `documents` 为待重排文档文本列表（召回候选）
        - 返回与 `documents` 等长的相关性分数（越高越相关）
        - 分数量纲由各实现决定（cross-encoder logits，可为负），仅用于内部排序/过滤
    """

    @abstractmethod
    def rerank(self, query: str, documents: list[str], batch_size: int = 32) -> list[float]:
        """对 (query, 每个 document) 逐对打分。

        Args:
            query: 查询文本
            documents: 候选文档文本列表
            batch_size: 批大小

        Returns:
            与 `documents` 等长的相关性分数列表（索引一一对应）。
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        """重排模型名（日志 / 状态展示）。"""
        raise NotImplementedError
