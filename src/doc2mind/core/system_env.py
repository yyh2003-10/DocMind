"""GPU 加速环境诊断与一键安装（system_env）。

提供两块能力：
- :func:`get_gpu_diagnosis`：综合探测 GPU / CUDA / onnxruntime 状态，
  给出推荐安装路径（cuda12 / cuda13 / directml / paddle-ocr-gpu / cpu）。
- :func:`install_gpu_packages`：按选定路径用 pip 安装对应加速包，
  流式产出日志事件（供 HTTP SSE 端点回传 WPF）。

已知坑（诊断与安装命令都围绕它设计）：
onnxruntime 与 onnxruntime-gpu 提供同名 Python 模块 ``onnxruntime``。
若两者同时被 pip 安装，后装的一方的模块目录会覆盖另一方，导致
``import onnxruntime`` 解析到 CPU 版、``get_available_providers()``
里没有 CUDAExecutionProvider。因此安装命令一律先卸载 CPU 版
``onnxruntime``，让 GPU 版独占同名模块。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncGenerator
from typing import Any

from doc2mind.core.embedder.fastembed_impl import get_embed_providers
from doc2mind.core.nvidia_runtime import cuda_runtime_ready, get_nvidia_driver_info

logger = logging.getLogger("doc2mind.system_env")

# 诊断关注的 pip 包 → importlib.metadata 查询名
_PACKAGE_KEYS: tuple[str, ...] = (
    "fastembed",
    "onnxruntime",
    "onnxruntime-gpu",
    "onnxruntime-directml",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cuda-runtime-cu13",
    "nvidia-cudnn-cu12",
    "nvidia-cudnn-cu13",
    "nvidia-cublas-cu12",
    "nvidia-cublas-cu13",
    "paddlepaddle",
    "paddlepaddle-gpu",
)

# 国内网络优先镜像（pip 常规安装）
_PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

# Python 版本 → paddle CUDA wheel 的 cp 标签（支持 3.9 ~ 3.12）
_PY_TAG: dict[tuple[int, int], str] = {
    (3, 9): "cp39",
    (3, 10): "cp310",
    (3, 11): "cp311",
    (3, 12): "cp312",
}

_GUI_INSTALLABLE = object()  # 哨兵：区分"该路径可安装"与"未知路径"


def _dist_versions(names: tuple[str, ...]) -> dict[str, str | None]:
    """查询已安装包版本，未安装返回 None（importlib.metadata）。"""
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str | None] = {}
    for name in names:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = None
    return out


def _py_wheel_tag() -> str | None:
    """当前 Python 对应的 cpXX 标签；不支持版本返回 None。"""
    ver = (sys.version_info.major, sys.version_info.minor)
    return _PY_TAG.get(ver)


def _is_gpu_provider(provider: str) -> bool:
    return (
        "CUDA" in provider
        or "Dml" in provider
        or "DML" in provider
        or "CoreML" in provider
    )


def get_gpu_diagnosis() -> dict[str, Any]:
    """综合探测当前 GPU / 硬件加速环境，产出跨平台诊断报告。"""
    providers = get_embed_providers()
    gpu_providers = [p for p in providers if _is_gpu_provider(p)]

    driver = get_nvidia_driver_info()
    runtime_ready, runtime_tag = cuda_runtime_ready()
    pkgs = _dist_versions(_PACKAGE_KEYS)

    warnings: list[str] = []

    # 覆盖问题：onnxruntime-gpu 与 onnxruntime（CPU）同名模块冲突
    ort_gpu = pkgs.get("onnxruntime-gpu")
    ort_cpu = pkgs.get("onnxruntime")
    if ort_gpu and ort_cpu and not gpu_providers:
        warnings.append(
            f"检测到 CPU 版 onnxruntime {ort_cpu} 与 onnxruntime-gpu {ort_gpu} "
            "并存，import 可能解析到 CPU 版（onnxruntime 当前无 CUDA provider）。"
            "点击「一键安装」会先卸载 CPU 版后再安装 GPU 包。"
        )
    if ort_gpu and runtime_ready and not gpu_providers:
        warnings.append(
            "onnxruntime-gpu 与 CUDA 运行时均已就绪，但 provider 未生效，"
            "大概率是 CPU 版 onnxruntime 覆盖了同名模块，请执行安装流程修复。"
        )
    if ort_gpu and not runtime_ready and sys.platform == "win32":
        warnings.append(
            "onnxruntime-gpu 已安装，但缺少匹配的 CUDA 运行时"
            f"（未找到 {', '.join(d for d, _ in _CUDA_RUNTIME_HINTS)}），"
            "请选择对应方案安装 nvidia 运行包。"
        )

    # 推荐路径（按操作系统与硬件智能匹配）
    if sys.platform == "darwin":
        recommended = "coreml" if "CoreMLExecutionProvider" in providers else "cpu"
    elif gpu_providers:
        provider = gpu_providers[0]
        if "CUDA" in provider:
            recommended = runtime_tag if runtime_tag else "cuda12"
        elif "CoreML" in provider:
            recommended = "coreml"
        else:
            recommended = "directml"
    elif driver:
        recommended = runtime_tag if runtime_tag else "cuda12"
        if runtime_tag == "cu13":
            warnings.append(
                "检测到 cu13 运行时：onnxruntime-gpu 需配套 cu13 构建的 wheel"
                "（PyPI 标准版为 cu12），请确认本地已有 cu13 wheel。"
            )
    elif sys.platform == "win32":
        recommended = "directml"
    else:
        recommended = "cpu"

    if not gpu_providers and not driver and sys.platform != "win32" and sys.platform != "darwin":
        recommended = "cpu"

    return {
        "gpu_available": bool(gpu_providers),
        "gpu_provider": gpu_providers[0] if gpu_providers else None,
        "embed_providers": providers,
        "has_nvidia_gpu": bool(driver),
        "gpu_name": (driver or {}).get("gpu_name") or ("Apple Silicon" if sys.platform == "darwin" else None),
        "driver_version": (driver or {}).get("driver_version"),
        "cuda_driver_version": (driver or {}).get("cuda_driver_version"),
        "cuda_runtime_ready": runtime_ready,
        "cuda_runtime_tag": runtime_tag,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "installed_packages": pkgs,
        "recommended_path": recommended,
        "warnings": warnings,
        "platform": sys.platform,
    }


# 诊断警告里用到的 DLL 提示（仅文案用）
_CUDA_RUNTIME_HINTS = (("cudart64_13.dll", "cu13"), ("cudart64_12.dll", "cu12"))


def _pip_install(argv: list[str]) -> list[str]:
    """构造 pip install 命令（当前 Python + 清华镜像）。"""
    return [sys.executable, "-m", "pip", "install", *argv, "-i", _PIP_MIRROR]


def _build_install_commands(path: str) -> list[list[str]] | None:
    """根据路径构造安装命令序列；未知路径返回 None。

    每个元素是一条完整命令（subprocess 直接执行，无需 shell）。
    """
    py = [sys.executable, "-m", "pip"]
    if path in ("cuda12", "cu12"):
        # 卸载 CPU 版 onnxruntime，避免其覆盖 onnxruntime-gpu 同名模块
        return [
            [*py, "uninstall", "onnxruntime", "-y"],
            _pip_install(
                [
                    "onnxruntime-gpu==1.28.0",
                    "nvidia-cuda-runtime-cu12",
                    "nvidia-cudnn-cu12",
                    "nvidia-cublas-cu12",
                ]
            ),
        ]
    if path in ("cuda13", "cu13"):
        return [
            [*py, "uninstall", "onnxruntime", "-y"],
            _pip_install(
                [
                    "nvidia-cuda-runtime-cu13",
                    "nvidia-cudnn-cu13",
                    "nvidia-cublas-cu13",
                ]
            ),
        ]
    if path == "directml":
        return [
            [*py, "uninstall", "onnxruntime", "onnxruntime-gpu", "-y"],
            _pip_install(["onnxruntime-directml"]),
        ]
    if path == "paddle-ocr-gpu":
        tag = _py_wheel_tag()
        if tag is None:
            return None
        url = (
            "https://mirrors.aliyun.com/paddlepaddle/3.3.1/win/"
            f"paddlepaddle_gpu-3.3.1-{tag}-{tag}-win_amd64.whl"
        )
        return [[*py, "install", url]]
    return None


async def install_gpu_packages(path: str) -> AsyncGenerator[dict[str, Any], None]:
    """按路径执行 GPU 加速包安装，流式产出事件字典。

    事件类型：
    - ``{"type": "log", "line": ...}``：一条命令/日志输出。
    - ``{"type": "error", "message": ...}``：安装失败（随后终止）。
    - ``{"type": "done", "success": true, "path": ...}``：全部完成。
    """
    cmds = _build_install_commands(path)
    if cmds is None:
        yield {"type": "error", "message": f"未知的安装路径: {path}"}
        return

    import subprocess

    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
        "creationflags": (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    }
    for cmd in cmds:
        yield {"type": "log", "line": "$ " + " ".join(cmd)}
        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                yield {"type": "log", "line": text}
        rc = await proc.wait()
        if rc != 0:
            yield {
                "type": "error",
                "message": f"pip 安装失败（退出码 {rc}），请查看上方日志排查。"
                "常见原因：网络/镜像不可达、wheel 与 Python 版本不匹配。",
            }
            return
        logger.info("GPU 安装步骤完成: %s", " ".join(cmd[:4]))
    yield {"type": "done", "success": True, "path": path}
