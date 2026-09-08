"""system_env 单测：OCR 安装路径构造 + 依赖状态聚合结构。

不实际执行 pip，只验证命令构造逻辑与返回字段契约。
"""

from __future__ import annotations

import asyncio
import json
import sys

from doc2mind.core.system_env import (
    _build_install_commands,
    get_dependencies_status,
)


class TestBuildInstallCommandsOcr:
    """OCR 安装命令构造（不执行，只校验 argv）。"""

    def test_paddle_ocr_cpu_path(self) -> None:
        cmds = _build_install_commands("paddle-ocr-cpu")
        assert cmds is not None
        assert len(cmds) == 1
        cmd = cmds[0]
        # 格式: [python, -m, pip, install, <packages...>, -i, <mirror>]
        assert "-m" in cmd and "pip" in cmd
        assert "install" in cmd
        # 应包含 paddlepaddle / paddleocr / pillow
        joined = " ".join(cmd)
        assert "paddlepaddle" in joined
        assert "paddleocr" in joined
        assert "pillow" in joined
        # 走清华镜像
        assert "pypi.tuna.tsinghua.edu.cn" in joined

    def test_ocr_cpu_alias(self) -> None:
        """ocr-cpu / ocr 别名应与 paddle-ocr-cpu 等价。"""
        for alias in ("ocr-cpu", "ocr"):
            cmds = _build_install_commands(alias)
            assert cmds is not None
            assert "paddleocr" in " ".join(cmds[0])

    def test_unknown_path_returns_none(self) -> None:
        assert _build_install_commands("nonexistent-path") is None


class TestGetDependenciesStatus:
    """get_dependencies_status 返回字段契约（不依赖真实 GPU）。"""

    def test_returns_required_fields(self) -> None:
        status = get_dependencies_status()
        # 必须包含所有前端面板需要的字段
        required = {
            "gpu_available",
            "gpu_provider",
            "has_nvidia_gpu",
            "cuda_runtime_ready",
            "ocr_available",
            "model_cached",
            "model_name",
            "poppler_available",
            "recommended_path",
            "installed_packages",
            "warnings",
            "platform",
            "python_version",
        }
        assert required.issubset(status.keys()), (
            f"缺失字段: {required - status.keys()}"
        )

    def test_field_types(self) -> None:
        status = get_dependencies_status()
        assert isinstance(status["gpu_available"], bool)
        assert isinstance(status["ocr_available"], bool)
        assert isinstance(status["model_cached"], bool)
        assert isinstance(status["poppler_available"], bool)
        assert isinstance(status["model_name"], str)
        assert isinstance(status["installed_packages"], dict)
        assert isinstance(status["warnings"], list)
        assert status["platform"] == sys.platform

    def test_python_version_format(self) -> None:
        status = get_dependencies_status()
        pv = status["python_version"]
        # 形如 "3.11.9"
        parts = pv.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)


# ======================================================================
# 安装命令构造：版本锁定与镜像回退
# ======================================================================
class TestInstallCommandPinning:
    """版本锁定口径一致性：GUI 路径不得自由解析。"""

    def test_pillow_pinned_in_ocr_cpu_path(self) -> None:
        from doc2mind.core.system_env import _build_install_commands

        cmds = _build_install_commands("ocr-cpu")
        assert cmds is not None
        joined = " ".join(cmds[0])
        assert "pillow==12.2.0" in joined, "pillow 必须锁定版本，避免 GUI 路径自由解析漂移"
        assert "paddlepaddle==3.3.1" in joined
        assert "paddleocr==3.7.0" in joined

    def test_mirror_parameter_overrides_default(self) -> None:
        from doc2mind.core.system_env import _build_install_commands

        cmds = _build_install_commands("ocr-cpu", mirror="https://pypi.org/simple")
        assert cmds is not None
        assert "https://pypi.org/simple" in cmds[0]

    def test_pip_index_url_respected_when_no_mirror(self, monkeypatch) -> None:
        """用户显式配置 PIP_INDEX_URL 时不得被硬编码镜像覆盖。"""
        from doc2mind.core import system_env

        monkeypatch.setenv("PIP_INDEX_URL", "https://my.corp/simple")
        cmds = system_env._build_install_commands("ocr-cpu")
        assert cmds is not None
        assert "https://my.corp/simple" in cmds[0]
        assert "pypi.tuna.tsinghua.edu.cn" not in cmds[0]


class TestIsWheelCompatible:
    """跨平台 wheel 兼容判定（旧实现 Windows 恒跳过平台检查）。"""

    def test_linux_wheel_rejected_on_windows(self, monkeypatch) -> None:
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env.sys, "platform", "win32")
        info = {"platform_tag": "linux_x86_64", "python_tag": "cp311", "abi_tag": "cp311"}
        assert system_env.is_wheel_compatible(info) is False

    def test_macos_wheel_rejected_on_windows(self, monkeypatch) -> None:
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env.sys, "platform", "win32")
        info = {"platform_tag": "macosx_11_0_arm64", "python_tag": "cp311", "abi_tag": "cp311"}
        assert system_env.is_wheel_compatible(info) is False

    def test_windows_wheel_accepted_on_windows(self, monkeypatch) -> None:
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env.sys, "platform", "win32")
        info = {"platform_tag": "win_amd64", "python_tag": "cp311", "abi_tag": "cp311"}
        assert system_env.is_wheel_compatible(info) is True

    def test_any_wheel_accepted_everywhere(self, monkeypatch) -> None:
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env.sys, "platform", "win32")
        info = {"platform_tag": "any", "python_tag": "py3", "abi_tag": "none"}
        assert system_env.is_wheel_compatible(info) is True

    def test_wrong_python_version_rejected(self, monkeypatch) -> None:
        from collections import namedtuple

        from doc2mind.core import system_env

        _VersionInfo = namedtuple("_VersionInfo", "major minor micro releaselevel serial")
        monkeypatch.setattr(system_env.sys, "platform", "win32")
        monkeypatch.setattr(system_env.sys, "version_info", _VersionInfo(3, 11, 0, "final", 0))
        info = {"platform_tag": "win_amd64", "python_tag": "cp312", "abi_tag": "cp312"}
        assert system_env.is_wheel_compatible(info) is False


# ======================================================================
# 安装执行链：退出码处理、失败分类、镜像重试、超时（fake subprocess）
# ======================================================================
class _FakeProc:
    """asyncio.create_subprocess_exec 的最小替身。"""

    def __init__(self, lines: list[str], rc: int, hang: bool = False):
        self._lines = [l.encode("utf-8") + b"\n" for l in lines]
        self._rc = rc
        self._hang = hang
        self.stdout = self
        self.killed = False

    async def readline(self) -> bytes:
        if self._hang:
            await asyncio.sleep(3600)
            return b""
        if self._lines:
            return self._lines.pop(0)
        return b""

    async def wait(self) -> int:
        return self._rc

    def kill(self) -> None:
        self.killed = True


def _collect(gen_factory) -> list[dict]:
    """同步驱动 async 生成器收集全部事件。"""

    async def _inner() -> list[dict]:
        out = []
        async for ev in gen_factory():
            out.append(ev)
        return out

    return asyncio.run(_inner())


class TestRunInstallCommands:
    def test_success_yields_done(self, monkeypatch) -> None:
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env, "_check_disk_space", lambda path: None)

        def _fake_exec(*cmd, **kwargs):
            fut = asyncio.Future()
            fut.set_result(_FakeProc(["Successfully installed"], 0))
            return fut

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        events = _collect(
            lambda: system_env._run_install_commands(
                lambda m: [[sys.executable, "-m", "pip", "install", "x"]], "ocr-cpu", "OCR"
            )
        )
        assert events[-1]["type"] == "done"
        assert events[-1]["success"] is True

    def test_file_lock_error_gets_targeted_message(self, monkeypatch) -> None:
        """WinError 5 必须给出「文件占用」专项指引，而非泛泛的网络/包冲突文案。"""
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env, "_check_disk_space", lambda path: None)

        def _fake_exec(*cmd, **kwargs):
            fut = asyncio.Future()
            fut.set_result(_FakeProc(
                ["ERROR: Could not install packages due to an OSError: [WinError 5] Access is denied"], 1
            ))
            return fut

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        events = _collect(
            lambda: system_env._run_install_commands(
                lambda m: [[sys.executable, "-m", "pip", "install", "x"]], "ocr-cpu", "OCR"
            )
        )
        assert events[-1]["type"] == "error"
        assert "占用" in events[-1]["message"]
        # 文案需同时覆盖「后端占用」与「安装目录无写权限」两种 WinError 5 成因
        assert "设置页" in events[-1]["message"]
        assert "管理员" in events[-1]["message"]

    def test_uninstall_failure_is_skipped(self, monkeypatch) -> None:
        """显式 pip uninstall 被占用失败 → 跳过并继续安装。"""
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env, "_check_disk_space", lambda path: None)

        def _fake_exec(*cmd, **kwargs):
            fut = asyncio.Future()
            if "uninstall" in cmd:
                fut.set_result(_FakeProc(["ERROR: Cannot uninstall", "[WinError 5]"], 1))
            else:
                fut.set_result(_FakeProc(["Successfully installed"], 0))
            return fut

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        cmds = [
            [sys.executable, "-m", "pip", "uninstall", "onnxruntime", "-y"],
            [sys.executable, "-m", "pip", "install", "x"],
        ]
        events = _collect(lambda: system_env._run_install_commands(lambda m: cmds, "cuda12", "GPU"))
        assert events[-1]["type"] == "done"
        assert any("已跳过卸载" in e.get("line", "") for e in events if e["type"] == "log")

    def test_network_failure_falls_back_to_next_mirror(self, monkeypatch) -> None:
        """网络类失败 → 自动切换下一镜像重试整序列。"""
        from doc2mind.core import system_env

        attempts: list[str] = []

        monkeypatch.setattr(system_env, "_check_disk_space", lambda path: None)

        def build(mirror):
            attempts.append(mirror)
            return [[sys.executable, "-m", "pip", "install", "x", "-i", mirror]]

        def _fake_exec(*cmd, **kwargs):
            fut = asyncio.Future()
            mirror = cmd[-1]
            if mirror.endswith("tsinghua.edu.cn/simple"):
                fut.set_result(_FakeProc(["ReadTimeoutError: Read timed out"], 1))
            else:
                fut.set_result(_FakeProc(["Successfully installed"], 0))
            return fut

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        events = _collect(lambda: system_env._run_install_commands(build, "ocr-cpu", "OCR"))
        assert events[-1]["type"] == "done"
        assert len(attempts) >= 2, "网络失败后应尝试下一个镜像"
        assert any("切换镜像重试" in e.get("line", "") for e in events if e["type"] == "log")

    def test_idle_timeout_kills_process(self, monkeypatch) -> None:
        """pip 空闲挂死 → 超时终止进程并产出 error 帧（旧实现会永久阻塞）。"""
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env, "_check_disk_space", lambda path: None)
        monkeypatch.setattr(system_env, "_INSTALL_IDLE_TIMEOUT_SEC", 0.1)
        proc = _FakeProc([], 0, hang=True)

        def _fake_exec(*cmd, **kwargs):
            fut = asyncio.Future()
            fut.set_result(proc)
            return fut

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        events = _collect(
            lambda: system_env._run_install_commands(
                lambda m: [[sys.executable, "-m", "pip", "install", "x"]], "ocr-cpu", "OCR"
            )
        )
        assert proc.killed, "超时后必须 kill 子进程"
        assert events[-1]["type"] == "error"
        assert "超时" in events[-1]["message"]


class TestOcrInstallIdempotency:
    """OCR 幂等保护：已安装时跳过重复安装（避免复发 numpy 文件锁问题）。"""

    def test_already_installed_skips(self, monkeypatch) -> None:
        from doc2mind.core import system_env

        monkeypatch.setattr(
            system_env,
            "_dist_versions",
            lambda names: {"paddlepaddle": "3.3.1", "paddlepaddle-gpu": None, "paddleocr": "3.7.0"},
        )

        def _boom_exec(*cmd, **kwargs):  # pragma: no cover — 不应触发真实安装
            raise AssertionError("已安装时应跳过，不应发起 pip")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom_exec)
        events = _collect(lambda: system_env.install_ocr_packages("ocr-cpu"))
        assert events[-1] == {"type": "done", "success": True, "path": "ocr-cpu"}
        assert any("已安装" in e.get("line", "") for e in events)

    def test_force_reinstalls(self, monkeypatch) -> None:
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env, "_check_disk_space", lambda path: None)

        monkeypatch.setattr(
            system_env,
            "_dist_versions",
            lambda names: {"paddlepaddle": "3.3.1", "paddlepaddle-gpu": None, "paddleocr": "3.7.0"},
        )

        def _fake_exec(*cmd, **kwargs):
            fut = asyncio.Future()
            fut.set_result(_FakeProc(["Successfully installed"], 0))
            return fut

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        events = _collect(lambda: system_env.install_ocr_packages("ocr-cpu", force=True))
        assert events[-1]["type"] == "done"

    def test_not_installed_proceeds(self, monkeypatch) -> None:
        from doc2mind.core import system_env

        monkeypatch.setattr(system_env, "_check_disk_space", lambda path: None)

        monkeypatch.setattr(
            system_env,
            "_dist_versions",
            lambda names: {"paddlepaddle": None, "paddlepaddle-gpu": None, "paddleocr": None},
        )

        def _fake_exec(*cmd, **kwargs):
            fut = asyncio.Future()
            fut.set_result(_FakeProc(["Successfully installed"], 0))
            return fut

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        events = _collect(lambda: system_env.install_ocr_packages("ocr-cpu"))
        assert events[-1]["type"] == "done"


class TestInstallCli:
    """独立安装 CLI：路径校验与状态标记。"""

    def test_unknown_path_returns_2(self, capsys) -> None:
        from doc2mind import install_cli

        assert install_cli.main(["nonexistent"]) == 2
        assert "未知" in capsys.readouterr().out

    def test_state_marker_written(self, tmp_path, monkeypatch) -> None:
        from doc2mind import install_cli

        state_file = tmp_path / "plugin_install.json"
        monkeypatch.setattr(install_cli, "_state_file", lambda: state_file)
        install_cli.main(["nonexistent"])  # 失败路径也会写标记
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["status"] == "failed"
        assert data["path"] == "nonexistent"

