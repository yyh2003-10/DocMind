"""分块器基础类型。

`Chunk` 是分块输出单元，也是嵌入引擎和向量存储的输入单元。
字段命名与 HTTP API 契约 (`docs/api.md`) 的 `Chunk` 对齐。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Chunk:
    """分块 — 一个可独立嵌入与检索的文本单元。

    Attributes:
        content: 分块文本（已 strip）
        tokens: 该分块的 token 数（嵌入前估算）
        metadata: 结构信息，继承自源 `DocumentElement` 并追加分块器写入的字段：
            - `type`: heading|paragraph|table|code|list|image
            - `heading`: 当前所属标题文本（语义分块写入）
            - `level`: 标题层级
            - `page` / `sheet` / `slide`: 来源定位
            - `chunk_index`: 在文档内的序号
            - `source_format`: 来源 DocFormat
            - `language`: 代码语言（code 分块）
            - `rows` / `cols`: 表格尺寸（table 分块）
    """

    content: str
    tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", self.content.strip())


class ChunkerError(Exception):
    """分块器异常。"""


class Chunker:
    """分块器抽象基类。

    子类实现 `chunk(elements) -> list[Chunk]`，输入是 `DocumentElement` 列表，
    输出是 `Chunk` 列表。
    """

    def chunk(self, elements: list) -> list[Chunk]:  # elements: list[DocumentElement]
        """把文档元素列表切成 Chunk 列表。"""
        raise NotImplementedError
