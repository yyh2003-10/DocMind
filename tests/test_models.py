"""核心数据模型单元测试。"""

from __future__ import annotations

from doc2mind.core.models import (
    DocFormat,
    DocumentElement,
    ElementType,
    LoadedDocument,
)


class TestDocFormat:
    def test_values(self) -> None:
        assert DocFormat.PDF.value == "pdf"
        assert DocFormat.DOCX.value == "docx"
        assert DocFormat.XLSX.value == "xlsx"
        assert DocFormat.PPTX.value == "pptx"
        assert DocFormat.MARKDOWN.value == "md"
        assert DocFormat.HTML.value == "html"
        assert DocFormat.IMAGE.value == "image"
        assert DocFormat.CODE.value == "code"
        assert DocFormat.UNKNOWN.value == "unknown"


class TestElementType:
    def test_values(self) -> None:
        assert ElementType.HEADING.value == "heading"
        assert ElementType.PARAGRAPH.value == "paragraph"
        assert ElementType.TABLE.value == "table"
        assert ElementType.TABLE_ROW.value == "table_row"
        assert ElementType.CODE.value == "code"
        assert ElementType.LIST.value == "list"
        assert ElementType.LIST_ITEM.value == "list_item"
        assert ElementType.IMAGE.value == "image"
        assert ElementType.UNKNOWN.value == "unknown"


class TestDocumentElement:
    def test_basic(self) -> None:
        el = DocumentElement(content="Hello", type=ElementType.PARAGRAPH)
        assert el.content == "Hello"
        assert el.type == ElementType.PARAGRAPH
        assert el.metadata == {}

    def test_strip_content(self) -> None:
        el = DocumentElement(content="  spaced  ", type=ElementType.PARAGRAPH)
        assert el.content == "spaced"

    def test_with_metadata(self) -> None:
        el = DocumentElement(
            content="Title",
            type=ElementType.HEADING,
            metadata={"level": 1, "page": 3},
        )
        assert el.metadata["level"] == 1
        assert el.metadata["page"] == 3

    def test_image_allows_empty(self) -> None:
        el = DocumentElement(content="", type=ElementType.IMAGE)
        assert el.content == ""

    def test_frozen(self) -> None:
        el = DocumentElement(content="x", type=ElementType.PARAGRAPH)
        import dataclasses

        assert dataclasses.fields(el)


class TestLoadedDocument:
    def test_basic(self) -> None:
        doc = LoadedDocument(
            source="test.pdf",
            format=DocFormat.PDF,
            elements=[
                DocumentElement(content="hello", type=ElementType.PARAGRAPH),
            ],
        )
        assert doc.source == "test.pdf"
        assert doc.format == DocFormat.PDF
        assert doc.element_count == 1

    def test_element_count(self) -> None:
        doc = LoadedDocument(
            source="a.docx",
            format=DocFormat.DOCX,
            elements=[
                DocumentElement(content="a", type=ElementType.PARAGRAPH),
                DocumentElement(content="b", type=ElementType.PARAGRAPH),
                DocumentElement(content="c", type=ElementType.PARAGRAPH),
            ],
        )
        assert doc.element_count == 3

    def test_defaults(self) -> None:
        doc = LoadedDocument(
            source="x.md",
            format=DocFormat.MARKDOWN,
            elements=[],
        )
        assert doc.page_count is None
        assert doc.size_bytes == 0
        assert doc.file_hash == ""
