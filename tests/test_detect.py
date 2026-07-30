"""文档格式检测单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2mind.core.loader.detect import detect_format, get_loader, is_supported


class TestDetectFormat:
    def test_pdf(self) -> None:
        assert detect_format(Path("report.pdf")).value == "pdf"

    def test_docx(self) -> None:
        assert detect_format(Path("letter.docx")).value == "docx"

    def test_xlsx(self) -> None:
        assert detect_format(Path("data.xlsx")).value == "xlsx"

    def test_pptx(self) -> None:
        assert detect_format(Path("slides.pptx")).value == "pptx"

    def test_md(self) -> None:
        assert detect_format(Path("readme.md")).value == "md"
        assert detect_format(Path("readme.markdown")).value == "md"

    def test_html(self) -> None:
        assert detect_format(Path("index.html")).value == "html"
        assert detect_format(Path("page.htm")).value == "html"

    def test_images(self) -> None:
        assert detect_format(Path("photo.png")).value == "image"
        assert detect_format(Path("photo.jpg")).value == "image"
        assert detect_format(Path("photo.jpeg")).value == "image"
        assert detect_format(Path("photo.bmp")).value == "image"
        assert detect_format(Path("photo.tiff")).value == "image"

    def test_code(self) -> None:
        assert detect_format(Path("main.py")).value == "code"
        assert detect_format(Path("app.js")).value == "code"
        assert detect_format(Path("main.ts")).value == "code"
        assert detect_format(Path("Main.java")).value == "code"
        assert detect_format(Path("main.c")).value == "code"
        assert detect_format(Path("main.cpp")).value == "code"
        assert detect_format(Path("main.cs")).value == "code"
        assert detect_format(Path("main.go")).value == "code"
        assert detect_format(Path("main.rs")).value == "code"
        assert detect_format(Path("main.rb")).value == "code"
        assert detect_format(Path("main.sh")).value == "code"
        assert detect_format(Path("main.sql")).value == "code"

    def test_unknown(self) -> None:
        assert detect_format(Path("file.xyz")).value == "unknown"

    def test_no_extension(self) -> None:
        assert detect_format(Path("README")).value == "unknown"


class TestIsSupported:
    def test_supported(self) -> None:
        assert is_supported(Path("test.pdf")) is True
        assert is_supported(Path("test.py")) is True

    def test_unsupported(self) -> None:
        assert is_supported(Path("test.xyz")) is False


class TestGetLoader:
    def test_pdf_loader(self) -> None:
        loader = get_loader(Path("test.pdf"))
        assert loader is not None

    def test_unknown_raises(self) -> None:
        from doc2mind.core.loader.base import UnsupportedFormatError

        with pytest.raises(UnsupportedFormatError):
            get_loader(Path("test.xyz"))

    def test_md_loader(self) -> None:
        loader = get_loader(Path("test.md"))
        assert loader is not None
