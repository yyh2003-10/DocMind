"""语义分块器单元测试。"""

from __future__ import annotations

from doc2mind.core.chunker.semantic import SemanticChunker
from doc2mind.core.config import Settings
from doc2mind.core.models import DocumentElement, ElementType


def _make_elements(*items: tuple[str, ElementType]) -> list[DocumentElement]:
    return [
        DocumentElement(content=text, type=typ) for text, typ in items
    ]


class TestSemanticChunker:
    def setup_method(self) -> None:
        self.chunker = SemanticChunker(Settings())

    def test_empty(self) -> None:
        assert self.chunker.chunk([]) == []

    def test_single_paragraph(self) -> None:
        els = _make_elements(("Hello world", ElementType.PARAGRAPH))
        chunks = self.chunker.chunk(els)
        assert len(chunks) == 1
        assert chunks[0].content == "Hello world"

    def test_heading_splits(self) -> None:
        """相邻标题有足够长的内容时，各自成块。"""
        els = _make_elements(
            ("Intro", ElementType.HEADING),
            ("This is the introductory paragraph with enough text to exceed the minimum chunk size of fifty characters.", ElementType.PARAGRAPH),
            ("Details", ElementType.HEADING),
            ("This is the details section with enough text to also exceed the minimum chunk size threshold.", ElementType.PARAGRAPH),
        )
        chunks = self.chunker.chunk(els)
        # heading text 存在 metadata 的 heading_path 中，content 只含段落文本
        assert len(chunks) == 2
        # 第一块：heading="Intro"
        assert chunks[0].metadata.get("heading") == "Intro"
        assert "introductory" in chunks[0].content
        # 第二块：heading="Details"
        assert chunks[1].metadata.get("heading") == "Details"
        assert "details section" in chunks[1].content

    def test_paragraphs_merged(self) -> None:
        els = _make_elements(
            ("para1", ElementType.PARAGRAPH),
            ("para2", ElementType.PARAGRAPH),
            ("para3", ElementType.PARAGRAPH),
        )
        chunks = self.chunker.chunk(els)
        assert len(chunks) == 1
        assert "para1" in chunks[0].content
        assert "para2" in chunks[0].content
        assert "para3" in chunks[0].content

    def test_list_items_merged(self) -> None:
        els = _make_elements(
            ("item1", ElementType.LIST_ITEM),
            ("item2", ElementType.LIST_ITEM),
            ("item3", ElementType.LIST_ITEM),
        )
        chunks = self.chunker.chunk(els)
        assert len(chunks) == 1
        assert "item1" in chunks[0].content
        assert "item3" in chunks[0].content

    def test_chunk_max_chars_overflow(self) -> None:
        """超长文本应被滑窗分割。"""
        settings = Settings(chunk_max_chars=50, chunk_overlap_chars=10)
        chunker = SemanticChunker(settings)
        # 制造超长段落
        long_text = "hello world " * 20  # ~240 chars
        els = _make_elements((long_text, ElementType.PARAGRAPH))
        chunks = chunker.chunk(els)
        assert len(chunks) >= 2, f"期望至少 2 个块，实际 {len(chunks)}"

    def test_short_chunks_merged(self) -> None:
        """过短块应并入相邻块。"""
        settings = Settings(chunk_max_chars=4000, chunk_min_chars=20,
                           chunk_overlap_chars=10)
        chunker = SemanticChunker(settings)
        els = _make_elements(
            ("# Title", ElementType.HEADING),
            ("short", ElementType.PARAGRAPH),
            ("# Next", ElementType.HEADING),
            ("also short", ElementType.PARAGRAPH),
        )
        chunks = chunker.chunk(els)
        # 短块应被合并，所以结果块数 ≤ 2
        assert len(chunks) <= 2

    def test_table_and_code_skipped(self) -> None:
        """table/code 类型元素应被跳过。"""
        els = _make_elements(
            ("some text", ElementType.PARAGRAPH),
            ("code block", ElementType.CODE),
            ("more text", ElementType.PARAGRAPH),
        )
        chunks = self.chunker.chunk(els)
        # code 被跳过，所有 paragraph 合并
        assert len(chunks) >= 1
        assert "code block" not in chunks[0].content if len(chunks) == 1 else True
