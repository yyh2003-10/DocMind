"""Windows NVIDIA GPU 运行时 DLL 目录注册（fastembed / paddleocr 共用）。

pip 安装的 nvidia-*-cu12/cu13 包把 CUDA 运行时 DLL（cudnn64_9.dll、
cublas64_12.dll、cudart64_12.dll 等）放在 ``site-packages/nvidia/<pkg>/bin``。
Windows 的 ``LoadLibrary`` 默认不会搜索这些目录，导致 onnxruntime CUDA EP /
paddleocr 创建会话时报告 ``LoadLibrary failed for cudnn64_9.dll`` / WinError
126/127 等错误，只能回退 CPU（GPU 加速完全失效）。

本模块把这些 bin 目录一次性注册进进程的 DLL 搜索路径
（``os.add_dll_directory``），必须在 import onnxruntime / paddle 之前调用。
重复调用是幂等的（进程级全局标记），可安全地在多个加载器间共享。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

_registered = False
_lock = threading.Lock()

# onnxruntime-gpu 1.28 存在 cu12（PyPI 标准 wheel）与 cu13（本机自定义构建）两种，
# 各自需要对应版本的 CUDA 运行时。按 DLL 名区分：cudart64_13.dll → cu13，
# cudart64_12.dll → cu12。检测时优先 cu13（构建方 dev 环境），再回退 cu12。
_CUDA_RUNTIME_DLLS: tuple[tuple[str, str], ...] = (
    ("cudart64_13.dll", "cu13"),
    ("cudart64_12.dll", "cu12"),
)


def register_nvidia_dll_dirs() -> None:
    """注册 site-packages/nvidia/<pkg>/bin 到 DLL 搜索路径与 PATH（幂等、线程安全）。

    非 Windows 平台直接返回；找不到 nvidia 目录时静默跳过。
    注意：Windows 下部分底层 C++ 依赖仅读取 PATH 环境变量，因此
    同时使用 os.add_dll_directory 和注入 os.environ["PATH"]。
    """
    global _registered
    if _registered:
        return
    with _lock:
        if _registered:
            return
        if os.name != "nt":
            _registered = True
            return
        try:
            import site  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            _registered = True
            return
        for sp in site.getsitepackages():
            nvidia_root = Path(sp) / "nvidia"
            if not nvidia_root.is_dir():
                continue
            for pkg_dir in sorted(nvidia_root.iterdir()):
                bin_dir = pkg_dir / "bin"
                if not bin_dir.is_dir():
                    continue
                bin_str = str(bin_dir)
                try:
                    os.add_dll_directory(bin_str)
                except (OSError, ValueError):
                    pass
                # 同时前置注入 PATH，确保 LoadLibrary 正常寻址
                if bin_str not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = bin_str + os.pathsep + os.environ.get("PATH", "")
        _registered = True


def cuda_runtime_ready() -> tuple[bool, str]:
    """检测 onnxruntime 所需的 CUDA 运行时是否真正就绪（Windows）。

    返回 ``(就绪, 版本标签)``，版本标签为 ``cu13`` / ``cu12`` / 空字符串。

    onnxruntime-gpu 1.28 有 cu12（PyPI 标准 wheel）与 cu13（自定义构建）两种
    变体。当环境里只有 cu12 运行时（例如与 paddle cu12 共存）时，直接把
    CUDAExecutionProvider 交给 cu13 构建的 onnxruntime 会加载到错误版本的
    cudnn/cublas，在 C 层直接崩溃（Python 无法捕获、进程退出）。
    因此选 CUDA provider 前必须预检：对应版本的运行时缺失就回退 CPU。

    非 Windows 平台返回 ``(True, "cu13")``（由 onnxruntime 自行管理系统库路径）。
    """
    if os.name != "nt":
        return True, "cu13"
    # 先注册 nvidia/<pkg>/bin 搜索路径，DLL 才可能被 LoadLibrary 找到
    register_nvidia_dll_dirs()
    try:
        import ctypes  # noqa: PLC0415

        for dll_name, tag in _CUDA_RUNTIME_DLLS:
            try:
                ctypes.WinDLL(dll_name)
                return True, tag
            except OSError:
                continue
    except Exception:  # noqa: BLE001 — 任何异常均视为运行时未就绪
        return False, ""
    return False, ""


def get_nvidia_driver_info() -> dict[str, str] | None:
    """探测 NVIDIA 显卡信息（GPU 名 / 驱动版本 / CUDA 驱动版本）。

    返回 ``{"gpu_name": ..., "driver_version": ..., "cuda_driver_version": ...}``；
    nvidia-smi 缺失、无 NVIDIA 显卡或超时失败时返回 ``None``。
    """
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        gpu = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            errors="replace",
        )
        if gpu.returncode != 0 or not gpu.stdout.strip():
            return None
        parts = [p.strip() for p in gpu.stdout.splitlines()[0].split(",")]
        gpu_name = parts[0] if parts else ""
        driver_version = parts[1] if len(parts) > 1 else ""

        # CUDA 驱动版本取自 nvidia-smi 头部（如 "CUDA Version: 13.2"）
        cuda_driver_version = ""
        full = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=5,
            errors="replace",
        )
        for line in full.stdout.splitlines():
            if "CUDA Version" in line:
                cuda_driver_version = (
                    line.split("CUDA Version:", 1)[-1].strip().rstrip("|").strip()
                )
                break
        return {
            "gpu_name": gpu_name,
            "driver_version": driver_version,
            "cuda_driver_version": cuda_driver_version,
        }
    except Exception:  # noqa: BLE001 — 探测失败返回 None，由诊断层处理
        return None