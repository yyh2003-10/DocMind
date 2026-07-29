"""核心数据模型 — 所有 Loader / Chunker / Store 共享的类型定义。

设计原则：
- 不可变（`dataclass(frozen=True)`），便于跨线程传递与缓存
- 字段命名与 HTTP API 契约 (`docs/api.md`) 对齐
- 元数据用 `dict[str, object]`，保留各 Loader 的结构信息
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DocFormat(str, Enum):
    """支持的文档格式（按扩展名映射）。"""

    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    MARKDOWN = "md"
    HTML = "html"
    IMAGE = "image"
    CODE = "code"
    UNKNOWN = "unknown"


class ElementType(str, Enum):
    """文档元素类型 — Loader 输出的语义标签。

    用于 Chunker 决策：
    - heading → 分块边界
    - table / table_row → 整表保护
    - code → 按函数分块
    - paragraph / list → 合并到当前块
    """

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    TABLE_ROW = "table_row"
    CODE = "code"
    LIST = "list"
    LIST_ITEM = "list_item"
    IMAGE = "image"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DocumentElement:
    """文档元素 — Loader 解析后的最小语义单元。

    Attributes:
        content: 元素文本内容（已去除首尾空白）
        type: 元素类型（heading/paragraph/table/code/...）
        metadata: 结构信息，常见键：
            - `level`: 标题层级 (1-6)，仅 heading
            - `page`: 来源页码 (1-based)，PDF/PPTX 有
            - `sheet`: 来源工作表名，XLSX 有
            - `slide`: 来源幻灯片序号，PPTX 有
            - `rows` / `cols`: 表格尺寸，table 有
            - `language`: 代码语言，code 有
            - `bbox`: 边界框 `[x0, y0, x1, y1]`，PDF 坐标
            - `source_format`: 来源格式 (DocFormat.value)
    """

    content: str
    type: ElementType
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # frozen dataclass 的字段只能在 __init__ 中设置；
        # 通过 object.__setattr__ 绕过冻结来规范化 content。
        object.__setattr__(self, "content", self.content.strip())
        if not self.content and self.type != ElementType.IMAGE:
            # 空元素允许构造（Loader 会过滤），但日志记录
            pass


@dataclass(frozen=True)
class LoadedDocument:
    """Loader 输出 — 整篇文档的元素列表 + 文件级元数据。

    Attributes:
        source: 原始文件名或路径（仅用于显示，不存绝对路径）
        format: 文档格式
        elements: 文档元素列表（按文档顺序）
        page_count: 总页数，PDF/DOCX/PPTX 有，其余为 None
        size_bytes: 文件字节数
        file_hash: MD5 哈希，用于增量去重
    """

    source: str
    format: DocFormat
    elements: list[DocumentElement]
    page_count: int | None = None
    size_bytes: int = 0
    file_hash: str = ""

    @property
    def element_count(self) -> int:
        """元素数量。"""
        return len(self.elements)
