"""system_env 单测：OCR 安装路径构造 + 依赖状态聚合结构。

不实际执行 pip，只验证命令构造逻辑与返回字段契约。
"""

from __future__ import annotations

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
