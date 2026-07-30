"""代码分块器单元测试。"""

from __future__ import annotations

from doc2mind.core.chunker.code import CodeChunker
from doc2mind.core.config import Settings
from doc2mind.core.models import DocumentElement, ElementType


class TestCodeChunker:
    def setup_method(self) -> None:
        self.chunker = CodeChunker(Settings())

    def test_empty(self) -> None:
        assert self.chunker.chunk([]) == []

    def test_simple_code_block(self) -> None:
        els = [
            DocumentElement(
                content="print('hello')",
                type=ElementType.CODE,
                metadata={"language": "python"},
            ),
        ]
        chunks = self.chunker.chunk(els)
        assert len(chunks) == 1
        assert "hello" in chunks[0].content

    def test_multiple_code_blocks_separately(self) -> None:
        els = [
            DocumentElement(
                content="fn main() { println!(\"hi\"); }",
                type=ElementType.CODE,
                metadata={"language": "rust"},
            ),
            DocumentElement(
                content="def foo(): pass",
                type=ElementType.CODE,
                metadata={"language": "python"},
            ),
        ]
        chunks = self.chunker.chunk(els)
        assert len(chunks) == 2

    def test_non_code_elements_skipped(self) -> None:
        """非 CODE 类型的元素应被忽略。"""
        els = [
            DocumentElement(content="text", type=ElementType.PARAGRAPH),
            DocumentElement(content="fn foo() {}", type=ElementType.CODE),
            DocumentElement(content="more text", type=ElementType.PARAGRAPH),
        ]
        chunks = self.chunker.chunk(els)
        assert len(chunks) == 1  # 只有 CODE 被处理
        assert "foo" in chunks[0].content
