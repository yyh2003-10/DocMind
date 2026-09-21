"""外部资源定位与配置链测试：poppler 优先级、config.toml 重定向、Settings 字段。

不依赖真实 poppler/python 安装，全部用临时目录构造候选验证。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# ======================================================================
# Settings.poppler_path 字段与环境变量映射
# ======================================================================
class TestSettingsPopplerPath:
    def test_default_empty(self) -> None:
        from doc2mind.core.config import Settings

        assert Settings().poppler_path == ""

    def test_env_mapping(self, monkeypatch) -> None:
        """DOC2MIND_POPPLER_PATH 环境变量自动映射到 poppler_path 字段。"""
        from doc2mind.core.config import Settings

        monkeypatch.setenv("DOC2MIND_POPPLER_PATH", r"C:\tools\poppler\Library\bin")
        assert Settings.from_env().poppler_path == r"C:\tools\poppler\Library\bin"


# ======================================================================
# DOC2MIND_CONFIG：config.toml 位置重定向
# ======================================================================
class TestConfigRedirect:
    def test_default_path(self, monkeypatch) -> None:
        from doc2mind.core import config

        monkeypatch.delenv("DOC2MIND_CONFIG", raising=False)
        assert config.config_file_path() == config._user_config_dir() / "config.toml"

    def test_env_override(self, monkeypatch, tmp_path) -> None:
        from doc2mind.core import config

        target = tmp_path / "my" / "config.toml"
        monkeypatch.setenv("DOC2MIND_CONFIG", str(target))
        assert config.config_file_path() == target

    def test_load_from_redirected_file(self, monkeypatch, tmp_path) -> None:
        """重定向后的 config.toml 内容应被正常读取。"""
        from doc2mind.core import config

        target = tmp_path / "custom.toml"
        target.write_text("[doc2mind]\nembed_model = \"test-model-x\"\n", encoding="utf-8")
        monkeypatch.setenv("DOC2MIND_CONFIG", str(target))
        monkeypatch.delenv("DOC2MIND_EMBED_MODEL", raising=False)
        data = config.load_config_file()
        assert data.get("embed_model") == "test-model-x"


# ======================================================================
# pdf_loader._find_poppler：显式配置优先级
# ======================================================================
class TestFindPopplerPriority:
    def test_configured_path_wins(self, tmp_path) -> None:
        """配置的 poppler_path（含 pdftoppm）必须优先于 PATH / 常见目录。"""
        import unittest.mock as mock

        import doc2mind.core.loader.pdf_loader as pdf_loader
        from doc2mind.core import config

        bin_dir = tmp_path / "poppler" / "Library" / "bin"
        bin_dir.mkdir(parents=True)
        anchor = "pdftoppm.exe" if os.name == "nt" else "pdftoppm"
        (bin_dir / anchor).write_text("fake", encoding="utf-8")

        cfg = config.Settings()
        cfg.poppler_path = str(bin_dir)
        with mock.patch.object(config, "get_settings", return_value=cfg):
            assert pdf_loader._find_poppler() == str(bin_dir)

    def test_invalid_config_falls_back(self, tmp_path) -> None:
        """配置指向的目录缺少 pdftoppm 时回退自动探测，不得崩溃。"""
        import shutil
        import unittest.mock as mock

        import doc2mind.core.loader.pdf_loader as pdf_loader
        from doc2mind.core import config

        cfg = config.Settings()
        cfg.poppler_path = str(tmp_path / "missing")
        with mock.patch.object(config, "get_settings", return_value=cfg):
            result = pdf_loader._find_poppler()
        # 回退后：要么 None（PATH 命中），要么某个真实存在的目录
        assert result is None or Path(result).is_dir()

    def test_empty_config_uses_auto_discovery(self, monkeypatch) -> None:
        """未配置时走原自动探测（PATH 命中 → None，交 pdf2image 处理）。"""
        import shutil
        import unittest.mock as mock

        import doc2mind.core.loader.pdf_loader as pdf_loader
        from doc2mind.core import config

        cfg = config.Settings()
        with mock.patch.object(config, "get_settings", return_value=cfg):
            result = pdf_loader._find_poppler()
        if shutil.which("pdftoppm") is not None:
            assert result is None
        # 否则结果为 None 或真实目录（本机环境相关，只断言不抛异常）
