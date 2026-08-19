"""GPU / OCR 加速环境诊断与一键安装（system_env）。

提供三块能力：
- :func:`get_gpu_diagnosis`：综合探测 GPU / CUDA / onnxruntime 状态，
  给出推荐安装路径（cuda12 / cuda13 / directml / paddle-ocr-gpu / cpu）。
- :func:`install_gpu_packages` / :func:`install_ocr_packages`：按选定路径用
  pip 安装对应加速包，流式产出日志事件（供 HTTP SSE 端点回传 WPF）。
- :func:`get_dependencies_status`：聚合 GPU / OCR / 嵌入模型 / poppler 的
  就绪状态，供设置页「环境自检」面板一次拉取。

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
from pathlib import Path
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


def parse_wheel_filename(filename: str) -> dict[str, Any] | None:
    """解析 wheel 文件名：{dist}-{version}(-{build})?-{python}-{abi}-{platform}.whl"""
    if not filename.lower().endswith(".whl"):
        return None
    stem = filename[:-4]
    parts = stem.split("-")
    if len(parts) < 5:
        return None
    plat_tag = parts[-1]
    abi_tag = parts[-2]
    py_tag = parts[-3]
    version = parts[1]
    dist = parts[0].replace("_", "-").lower()
    return {
        "name": dist,
        "version": version,
        "python_tag": py_tag,
        "abi_tag": abi_tag,
        "platform_tag": plat_tag,
        "filename": filename,
    }


def is_wheel_compatible(info: dict[str, Any]) -> bool:
    """检查 wheel 是否与当前平台及 Python 版本兼容。"""
    plat = info["platform_tag"].lower()
    if plat != "any" and plat != "none_any" and "win_amd64" not in plat:
        if sys.platform != "win32" and plat not in sys.platform:
            return False

    py = info["python_tag"].lower()
    current_major, current_minor = sys.version_info.major, sys.version_info.minor
    current_tag = f"cp{current_major}{current_minor}"

    if "py3" in py or "py2.py3" in py or "none" in py:
        return True
    return current_tag in py


def scan_local_wheels(extra_dirs: list[str | Path] | None = None) -> list[dict[str, Any]]:
    r"""扫描本地多层级目录获取所有可用的本地 wheel 文件。

    优先级与搜索路径：
    1. 环境变量 ``DOCMIND_WHEELS_DIR``
    2. 工作区及子目录 ``./wheels``, ``./pkgs``, ``./download``
    3. 用户 Downloads 目录
    4. pip 缓存与本地盘符根目录 ``C:\wheels``, ``D:\wheels``, ``E:\wheels``, ``%TEMP%``
    """
    candidate_dirs: list[Path] = []

    env_dir = os.getenv("DOCMIND_WHEELS_DIR")
    if env_dir:
        candidate_dirs.append(Path(env_dir))

    if extra_dirs:
        for ed in extra_dirs:
            candidate_dirs.append(Path(ed))

    cwd = Path.cwd()
    candidate_dirs.extend([
        cwd / "wheels",
        cwd / "pkgs",
        cwd / "download",
        cwd.parent / "wheels",
        Path(__file__).resolve().parents[3] / "wheels",
    ])

    candidate_dirs.extend([
        Path.home() / "Downloads",
        Path(os.path.expandvars(r"%TEMP%")),
        Path(r"C:\wheels"),
        Path(r"D:\wheels"),
        Path(r"E:\wheels"),
    ])

    seen_dirs: set[str] = set()
    found: dict[str, dict[str, Any]] = {}

    for cdir in candidate_dirs:
        try:
            resolved = cdir.resolve()
            if not resolved.is_dir() or str(resolved) in seen_dirs:
                continue
            seen_dirs.add(str(resolved))

            for item in resolved.iterdir():
                if item.is_file() and item.name.lower().endswith(".whl"):
                    info = parse_wheel_filename(item.name)
                    if info and is_wheel_compatible(info):
                        pkg_name = info["name"]
                        size_mb = round(item.stat().st_size / (1024 * 1024), 2)
                        found[pkg_name] = {
                            "name": pkg_name,
                            "version": info["version"],
                            "path": str(item.resolve()),
                            "filename": item.name,
                            "dir": str(item.parent.resolve()),
                            "size_mb": size_mb,
                        }
                elif item.is_dir() and item.name.lower() in ("wheels", "pkgs", "cu12", "cu13", "gpu", "cuda"):
                    for sub in item.iterdir():
                        if sub.is_file() and sub.name.lower().endswith(".whl"):
                            info = parse_wheel_filename(sub.name)
                            if info and is_wheel_compatible(info):
                                pkg_name = info["name"]
                                size_mb = round(sub.stat().st_size / (1024 * 1024), 2)
                                found[pkg_name] = {
                                    "name": pkg_name,
                                    "version": info["version"],
                                    "path": str(sub.resolve()),
                                    "filename": sub.name,
                                    "dir": str(sub.parent.resolve()),
                                    "size_mb": size_mb,
                                }
        except Exception:
            pass

    return list(found.values())


def get_gpu_diagnosis() -> dict[str, Any]:
    """综合探测当前 GPU / 硬件加速环境，产出跨平台诊断报告。"""
    providers = get_embed_providers()
    gpu_providers = [p for p in providers if _is_gpu_provider(p)]

    driver = get_nvidia_driver_info()
    runtime_ready, runtime_tag = cuda_runtime_ready()
    pkgs = _dist_versions(_PACKAGE_KEYS)
    local_wheels = scan_local_wheels()

    warnings: list[str] = []

    if local_wheels:
        names_str = ", ".join(f"{w['name']} ({w['version']})" for w in local_wheels[:4])
        warnings.append(
            f"已在本地检测到 {len(local_wheels)} 个离线安装包（{names_str}），"
            "执行安装时将优先极速从本地安装，无需重复下载。"
        )

    # 覆盖问题：onnxruntime-gpu 与 onnxruntime（CPU）同名模块冲突
    ort_gpu = pkgs.get("onnxruntime-gpu")
    ort_cpu = pkgs.get("onnxruntime")
    if ort_gpu and ort_cpu and not gpu_providers:
        warnings.append(
            f"检测到 CPU 版 onnxruntime {ort_cpu} 与 onnxruntime-gpu {ort_gpu} "
            "并存，import 可能解析到 CPU 版（onnxruntime 当前无 CUDA provider）。"
            "点击「一键安装」会重新执行 GPU 加速包绑定覆盖。"
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
        "local_wheels_found": local_wheels,
        "recommended_path": recommended,
        "warnings": warnings,
        "platform": sys.platform,
    }


# 诊断警告里用到的 DLL 提示（仅文案用）
_CUDA_RUNTIME_HINTS = (("cudart64_13.dll", "cu13"), ("cudart64_12.dll", "cu12"))


def _pip_install(argv: list[str], find_links_dirs: list[str] | None = None) -> list[str]:
    """构造 pip install 命令（优先使用本地 find-links 目录 + 清华镜像）。"""
    cmd = [sys.executable, "-m", "pip", "install", *argv]
    if find_links_dirs:
        for d in find_links_dirs:
            cmd.extend(["--find-links", d])
    cmd.extend(["-i", _PIP_MIRROR])
    return cmd


def _build_install_commands(path: str) -> list[list[str]] | None:
    """根据路径构造安装命令序列（离线优先 + 网络回退）；未知路径返回 None。

    每个元素是一条完整命令（subprocess 直接执行，无需 shell）。
    """
    py = [sys.executable, "-m", "pip"]
    local_wheels = scan_local_wheels()
    local_map = {w["name"]: w for w in local_wheels}
    find_dirs = sorted({w["dir"] for w in local_wheels})

    if path in ("cuda12", "cu12"):
        req_pkgs = [
            "onnxruntime-gpu==1.28.0",
            "nvidia-cuda-runtime-cu12",
            "nvidia-cudnn-cu12",
            "nvidia-cublas-cu12",
        ]
        # 检查是否全部有本地 wheel
        local_files = [local_map[k]["path"] for k in ("onnxruntime-gpu", "nvidia-cuda-runtime-cu12", "nvidia-cudnn-cu12", "nvidia-cublas-cu12") if k in local_map]
        if len(local_files) == 4:
            return [
                [*py, "uninstall", "onnxruntime", "-y"],
                [*py, "install", *local_files, "--no-index"],
            ]
        return [
            [*py, "uninstall", "onnxruntime", "-y"],
            _pip_install(req_pkgs, find_links_dirs=find_dirs),
        ]

    if path in ("cuda13", "cu13"):
        req_pkgs = [
            "nvidia-cuda-runtime-cu13",
            "nvidia-cudnn-cu13",
            "nvidia-cublas-cu13",
        ]
        local_files = [local_map[k]["path"] for k in ("nvidia-cuda-runtime-cu13", "nvidia-cudnn-cu13", "nvidia-cublas-cu13") if k in local_map]
        if len(local_files) == 3:
            return [
                [*py, "uninstall", "onnxruntime", "-y"],
                [*py, "install", *local_files, "--no-index"],
            ]
        return [
            [*py, "uninstall", "onnxruntime", "-y"],
            _pip_install(req_pkgs, find_links_dirs=find_dirs),
        ]

    if path == "directml":
        if "onnxruntime-directml" in local_map:
            return [
                [*py, "uninstall", "onnxruntime", "onnxruntime-gpu", "-y"],
                [*py, "install", local_map["onnxruntime-directml"]["path"], "--no-index"],
            ]
        return [
            [*py, "uninstall", "onnxruntime", "onnxruntime-gpu", "-y"],
            _pip_install(["onnxruntime-directml"], find_links_dirs=find_dirs),
        ]

    if path == "paddle-ocr-gpu":
        if "paddlepaddle-gpu" in local_map:
            return [[*py, "install", local_map["paddlepaddle-gpu"]["path"], "--no-index"]]
        tag = _py_wheel_tag()
        if tag is None:
            return None
        url = (
            "https://mirrors.aliyun.com/paddlepaddle/3.3.1/win/"
            f"paddlepaddle_gpu-3.3.1-{tag}-{tag}-win_amd64.whl"
        )
        return [[*py, "install", url]]

    if path in ("paddle-ocr-cpu", "ocr-cpu", "ocr"):
        return [
            _pip_install(["paddlepaddle==3.3.1", "paddleocr==3.7.0", "pillow"], find_links_dirs=find_dirs),
        ]

    return None


async def install_gpu_packages(path: str) -> AsyncGenerator[dict[str, Any], None]:
    """按路径执行 GPU 加速包安装，流式产出事件字典。

    具备 Windows 运行态文件锁定容错与全流程日志持久化。
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
        cmd_str = " ".join(cmd)
        is_uninstall = len(cmd) >= 4 and cmd[1:3] == ["-m", "pip"] and cmd[3] == "uninstall"
        yield {"type": "log", "line": "$ " + cmd_str}
        logger.info("执行 GPU 安装命令: %s", cmd_str)

        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        assert proc.stdout is not None
        recent_lines: list[str] = []
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                recent_lines.append(text)
                if len(recent_lines) > 15:
                    recent_lines.pop(0)
                yield {"type": "log", "line": text}

        rc = await proc.wait()
        if rc != 0:
            if is_uninstall:
                warn_msg = "卸载前代组件被系统占用锁定，已跳过卸载并继续尝试覆盖安装..."
                logger.warning("GPU 卸载步骤跳过（退出码 %d）: %s", rc, warn_msg)
                yield {"type": "log", "line": f"[提示] {warn_msg}"}
                continue

            err_details = "\n".join(recent_lines[-5:]) if recent_lines else f"退出码 {rc}"
            logger.error("GPU 安装步骤失败（退出码 %d）: %s\n详细输出:\n%s", rc, cmd_str, err_details)
            yield {
                "type": "error",
                "message": f"pip 安装失败（退出码 {rc}）：\n{err_details}\n常见原因：网络不可达、包冲突或 wheel 不兼容。",
            }
            return
        logger.info("GPU 安装步骤完成: %s", " ".join(cmd[:4]))
    yield {"type": "done", "success": True, "path": path}


async def install_ocr_packages(path: str = "cpu") -> AsyncGenerator[dict[str, Any], None]:
    """按路径执行 OCR 依赖安装，流式产出事件字典。"""
    cmds = _build_install_commands(path)
    if cmds is None:
        yield {"type": "error", "message": f"未知的 OCR 安装路径: {path}"}
        return

    import subprocess

    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
        "creationflags": (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    }
    for cmd in cmds:
        cmd_str = " ".join(cmd)
        is_uninstall = len(cmd) >= 4 and cmd[1:3] == ["-m", "pip"] and cmd[3] == "uninstall"
        yield {"type": "log", "line": "$ " + cmd_str}
        logger.info("执行 OCR 安装命令: %s", cmd_str)

        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        assert proc.stdout is not None
        recent_lines: list[str] = []
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if text:
                recent_lines.append(text)
                if len(recent_lines) > 15:
                    recent_lines.pop(0)
                yield {"type": "log", "line": text}

        rc = await proc.wait()
        if rc != 0:
            if is_uninstall:
                warn_msg = "卸载前代组件被系统占用锁定，已跳过卸载并继续尝试覆盖安装..."
                logger.warning("OCR 卸载步骤跳过（退出码 %d）: %s", rc, warn_msg)
                yield {"type": "log", "line": f"[提示] {warn_msg}"}
                continue

            err_details = "\n".join(recent_lines[-5:]) if recent_lines else f"退出码 {rc}"
            logger.error("OCR 安装步骤失败（退出码 %d）: %s\n详细输出:\n%s", rc, cmd_str, err_details)
            yield {
                "type": "error",
                "message": f"OCR 依赖安装失败（退出码 {rc}）：\n{err_details}\n常见原因：网络不可达或 wheel 不兼容。",
            }
            return
        logger.info("OCR 安装步骤完成: %s", " ".join(cmd[:4]))
    yield {"type": "done", "success": True, "path": path}

    yield {"type": "done", "success": True, "path": path}


# --- 依赖状态聚合（供设置页「环境自检」面板）---
def _ocr_available() -> bool:
    """检测 OCR 依赖是否可用（try import，不加载模型，轻量）。"""
    try:
        import paddle  # noqa: F401
        import paddleocr  # noqa: F401
        return True
    except ImportError:
        return False
    except Exception:  # noqa: BLE001 — paddle 初始化失败也视为不可用
        return False


def _poppler_available() -> bool:
    """检测 poppler（pdf2image 依赖）是否可用。"""
    try:
        from doc2mind.core.config import get_settings

        poppler_path = getattr(get_settings(), "poppler_path", None)
        if poppler_path:
            from pathlib import Path

            p = Path(poppler_path)
            if p.is_dir() and (p / ("pdftoppm.exe" if os.name == "nt" else "pdftoppm")).is_file():
                return True
        # 检查 PATH
        import shutil

        return shutil.which("pdftoppm") is not None
    except Exception:  # noqa: BLE001
        return False


def get_dependencies_status() -> dict[str, Any]:
    """聚合返回所有依赖的就绪状态，供前端「环境自检」面板一次拉取。

    返回字段：
    - gpu_available / gpu_provider / cuda_runtime_ready / cuda_runtime_tag
    - ocr_available
    - model_cached / model_name
    - poppler_available
    - recommended_path（GPU 安装推荐路径）
    - installed_packages（pip 包版本快照）
    - warnings
    """
    from doc2mind.core.config import get_settings
    from doc2mind.core.embedder.fastembed_impl import is_model_cached

    diag = get_gpu_diagnosis()
    settings = get_settings()

    return {
        "gpu_available": diag.get("gpu_available", False),
        "gpu_provider": diag.get("gpu_provider"),
        "has_nvidia_gpu": diag.get("has_nvidia_gpu", False),
        "gpu_name": diag.get("gpu_name"),
        "cuda_runtime_ready": diag.get("cuda_runtime_ready", False),
        "cuda_runtime_tag": diag.get("cuda_runtime_tag"),
        "recommended_path": diag.get("recommended_path"),
        "ocr_available": _ocr_available(),
        "model_cached": is_model_cached(),
        "model_name": settings.embed_model,
        "poppler_available": _poppler_available(),
        "installed_packages": diag.get("installed_packages", {}),
        "warnings": diag.get("warnings", []),
        "platform": diag.get("platform"),
        "python_version": diag.get("python_version"),
    }
