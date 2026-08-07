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
import threading
from pathlib import Path

_registered = False
_lock = threading.Lock()


def register_nvidia_dll_dirs() -> None:
    """注册 site-packages/nvidia/<pkg>/bin 到 DLL 搜索路径（幂等、线程安全）。

    非 Windows 平台直接返回；找不到 nvidia 目录时静默跳过。
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
                try:
                    os.add_dll_directory(str(bin_dir))
                except (OSError, ValueError):
                    # 重复注册 / 路径无效：忽略单个包的失败
                    pass
        _registered = True


def cuda_runtime_ready() -> bool:
    """检测 onnxruntime 所需的 CUDA 运行时是否真正就绪（Windows）。

    onnxruntime-gpu 1.28 是 CUDA 13 构建，依赖 ``cudart64_13.dll`` 等
    cu13 运行时。当环境里只有 cu12 运行时（例如与 paddle cu12 共存）时，
    直接把 CUDAExecutionProvider 交给 onnxruntime 会加载到错误版本的
    cudnn/cublas，在 C 层直接崩溃（Python 无法捕获、进程退出）。
    因此选 CUDA provider 前必须预检：cu13 运行时缺失就回退 CPU。

    非 Windows 平台返回 True（由 onnxruntime 自行管理系统库路径）。
    """
    if os.name != "nt":
        return True
    try:
        import ctypes  # noqa: PLC0415

        ctypes.WinDLL("cudart64_13.dll")
        return True
    except Exception:  # noqa: BLE001 — 找不到 cudart64_13 即视为运行时未就绪
        return False
