"""格式转换器单元测试。"""

from __future__ import annotations

import pytest

from doc2mind.core.converter.formatter import (
    SUPPORTED_FORMATS,
    convert_document,
    convert_elements,
)
from doc2mind.core.models import DocFormat, DocumentElement, ElementType, LoadedDocument


class TestConvertElements:
    def test_to_text(self) -> None:
        els = [
            DocumentElement(content="Hello", type=ElementType.PARAGRAPH),
        ]
        result = convert_elements(els, "test.txt", "txt")
        assert "Hello" in result

    def test_to_md(self) -> None:
        els = [
            DocumentElement(content="Title", type=ElementType.HEADING, metadata={"level": 1}),
            DocumentElement(content="Body text", type=ElementType.PARAGRAPH),
        ]
        result = convert_elements(els, "test.md", "md")
        assert "# Title" in result
        assert "Body text" in result

    def test_to_json(self) -> None:
        els = [
            DocumentElement(content="hello", type=ElementType.PARAGRAPH),
        ]
        result = convert_elements(els, "test.json", "json")
        import json
        data = json.loads(result)
        assert data["source"] == "test.json"
        assert data["element_count"] == 1
        assert len(data["elements"]) == 1
        assert data["elements"][0]["content"] == "hello"

    def test_to_html(self) -> None:
        els = [
            DocumentElement(content="Hello", type=ElementType.PARAGRAPH),
        ]
        result = convert_elements(els, "test.html", "html")
        assert "<html" in result.lower() or "<!DOCTYPE" in result
        assert "Hello" in result

    def test_unsupported_format(self) -> None:
        from doc2mind.core.converter.formatter import ConversionError
        with pytest.raises(ConversionError):
            convert_elements([], "test.bin", "bin")


class TestConvertDocument:
    def test_roundtrip(self) -> None:
        doc = LoadedDocument(
            source="test.docx",
            format=DocFormat.DOCX,
            elements=[
                DocumentElement(content="Hello", type=ElementType.PARAGRAPH),
            ],
        )
        result = convert_document(doc, "md")
        assert "Hello" in result

    def test_supported_formats(self) -> None:
        for fmt in SUPPORTED_FORMATS:
            doc = LoadedDocument(
                source=f"test.{fmt}",
                format=DocFormat.UNKNOWN,
                elements=[],
            )
            result = convert_document(doc, fmt)
            assert result is not None
