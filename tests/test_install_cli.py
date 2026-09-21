"""install_cli 编码健壮性测试：GBK stdout 下回显含 U+FFFD 的 pip 输出不得崩溃。

回归背景：pip 的 GBK 中文输出经 UTF-8 解码产生替换符 U+FFFD，CLI 回显时
stdout 默认 GBK 编不了该字符 → UnicodeEncodeError → 安装中途崩溃
（真实事故：pillow 卸载成功后整个安装异常退出）。
"""

from __future__ import annotations

import asyncio
import io
import sys


def test_cli_survives_unencodable_output_on_gbk_stdout(monkeypatch) -> None:
    from doc2mind import install_cli
    from doc2mind.core import system_env

    async def fake_install(path: str, mirrors=None, force: bool = False):
        # 模拟 system_env 的真实输出：GBK 字节被 utf-8 replace 解码后的行
        yield {"type": "log", "line": "Attempting uninstall: pillow \ufffd 已卸载\ufffd"}
        yield {"type": "done", "success": True, "path": path}

    monkeypatch.setattr(system_env, "install_ocr_packages", fake_install)

    # 用 GBK 严格编码的 stdout 替换真实 stdout，模拟 Windows 控制台默认环境
    gbk_stdout = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
    monkeypatch.setattr(sys, "stdout", gbk_stdout)

    rc = install_cli.main(["ocr-cpu"])

    assert rc == 0, "含 U+FFFD 的日志行在 GBK stdout 下必须降级输出而不是崩溃"
    out = gbk_stdout.buffer.getvalue().decode("utf-8", errors="replace")
    assert "[完成]" in out


def test_cli_reconfigures_stdout_to_utf8(monkeypatch) -> None:
    from doc2mind import install_cli

    captured: dict = {}

    class FakeStd(io.StringIO):
        def reconfigure(self, **kwargs):
            captured.update(kwargs)

    async def fake_install(path: str, mirrors=None, force: bool = False):
        yield {"type": "done", "success": True, "path": path}

    from doc2mind.core import system_env

    monkeypatch.setattr(system_env, "install_ocr_packages", fake_install)
    monkeypatch.setattr(sys, "stdout", FakeStd())
    monkeypatch.setattr(sys, "stderr", FakeStd())

    install_cli.main(["ocr-cpu"])

    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"
