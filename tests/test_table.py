"""表格分块器单元测试。"""

from __future__ import annotations

from doc2mind.core.chunker.table import TableChunker
from doc2mind.core.models import DocumentElement, ElementType


class TestTableChunker:
    def setup_method(self) -> None:
        self.chunker = TableChunker()

    def test_empty(self) -> None:
        assert self.chunker.chunk([]) == []

    def test_single_table(self) -> None:
        els = [
            DocumentElement(content="| a | b |", type=ElementType.TABLE_ROW),
            DocumentElement(content="| 1 | 2 |", type=ElementType.TABLE_ROW),
        ]
        chunks = self.chunker.chunk(els)
        assert len(chunks) == 1
        assert "| a | b |" in chunks[0].content
        assert "| 1 | 2 |" in chunks[0].content

    def test_multiple_tables_separate(self) -> None:
        """连续 table_row 归为一张表，被非表格元素隔开则分为多表。"""
        els = [
            DocumentElement(content="row1", type=ElementType.TABLE_ROW),
            DocumentElement(content="row2", type=ElementType.TABLE_ROW),
            DocumentElement(content="sep", type=ElementType.PARAGRAPH),
            DocumentElement(content="rowA", type=ElementType.TABLE_ROW),
        ]
        chunks = self.chunker.chunk(els)
        # separator 被跳过，table 元素被 TABLE_ROW 分组
        assert len(chunks) == 2  # 两组 TABLE_ROW

    def test_table_element_as_single(self) -> None:
        """TABLE 类型的元素（含 rows/cols 元数据）作为整表。"""
        els = [
            DocumentElement(
                content="full table content",
                type=ElementType.TABLE,
                metadata={"rows": 5, "cols": 3},
            ),
        ]
        chunks = self.chunker.chunk(els)
        assert len(chunks) == 1
        assert chunks[0].content == "full table content"
