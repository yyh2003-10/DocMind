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

# 注意：本模块被 install_cli（独立安装进程）复用，禁止在模块顶层导入
# fastembed/numpy 等重依赖——安装进程一旦加载 numpy DLL，其子进程 pip
# 替换 numpy 文件时会再踩一次 WinError 5（文件被占用）。相关导入一律
# 延迟到 get_gpu_diagnosis() 函数体内。
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

# 国内网络优先镜像（pip 常规安装），按顺序回退：清华 → 阿里云 → 官方 PyPI。
# 用户显式配置了 PIP_INDEX_URL 时不再覆盖（见 _pip_install）。
_PIP_MIRRORS: tuple[str, ...] = (
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.org/simple",
)
_DEFAULT_MIRROR = _PIP_MIRRORS[0]
# Paddle 官方 GPU 稳定源（paddlepaddle-gpu cu126 whl 仅此处提供）
_PADDLE_GPU_INDEX = "https://www.paddlepaddle.org.cn/packages/stable/cu126/"

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


# sys.platform → 当前平台 wheel 标签的公共前缀（is_wheel_compatible 用）
_PLATFORM_HINTS: dict[str, str] = {
    "win32": "win",
    "darwin": "macosx",
    "linux": "linux",
}


def is_wheel_compatible(info: dict[str, Any]) -> bool:
    """检查 wheel 是否与当前平台及 Python 版本兼容。

    平台判定统一走 _PLATFORM_HINTS 前缀匹配：Windows 上必须含 "win"、
    macOS 上必须含 "macosx"、Linux 上必须含 "linux"，否则视为不兼容。
    （旧实现 `sys.platform != "win32"` 的条件让 Windows 恒跳过平台检查，
    linux/macos wheel 会被误判为兼容，pip 随后报 No matching distribution。）
    """
    plat = info["platform_tag"].lower()
    if plat not in ("any", "none_any"):
        hint = _PLATFORM_HINTS.get(sys.platform)
        if hint is None or hint not in plat:
            return False

    py = info["python_tag"].lower()
    current_major, current_minor = sys.version_info.major, sys.version_info.minor
    current_tag = f"cp{current_major}{current_minor}"

    if "py3" in py or "py2.py3" in py:
        return True
    # abi3 / cp3x-abi3 wheel 对同主版本系列通用；none（纯 py）已在上面放行
    if py == "none" or "abi3" in info["abi_tag"].lower():
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
    # 延迟导入：fastembed → onnxruntime/numpy，安装 CLI 复用本模块时不可提前加载
    from doc2mind.core.embedder.fastembed_impl import get_embed_providers

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


def _pip_install(
    argv: list[str],
    find_links_dirs: list[str] | None = None,
    mirror: str | None = None,
) -> list[str]:
    """构造 pip install 命令（优先使用本地 find-links 目录 + 指定镜像）。

    mirror 为 None 时尊重用户显式配置的 PIP_INDEX_URL；都未配置才用默认清华源。
    """
    cmd = [sys.executable, "-m", "pip", "install", *argv]
    if find_links_dirs:
        for d in find_links_dirs:
            cmd.extend(["--find-links", d])
    effective = mirror or os.environ.get("PIP_INDEX_URL") or _DEFAULT_MIRROR
    cmd.extend(["-i", effective])
    return cmd


def _build_install_commands(path: str, mirror: str | None = None) -> list[list[str]] | None:
    """根据路径构造安装命令序列（离线优先 + 网络回退）；未知路径返回 None。

    每个元素是一条完整命令（subprocess 直接执行，无需 shell）。
    mirror 参数供镜像回退重试用（见 _run_install_commands）。
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
            _pip_install(req_pkgs, find_links_dirs=find_dirs, mirror=mirror),
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
            _pip_install(req_pkgs, find_links_dirs=find_dirs, mirror=mirror),
        ]

    if path == "directml":
        if "onnxruntime-directml" in local_map:
            return [
                [*py, "uninstall", "onnxruntime", "onnxruntime-gpu", "-y"],
                [*py, "install", local_map["onnxruntime-directml"]["path"], "--no-index"],
            ]
        return [
            [*py, "uninstall", "onnxruntime", "onnxruntime-gpu", "-y"],
            _pip_install(["onnxruntime-directml"], find_links_dirs=find_dirs, mirror=mirror),
        ]

    if path in ("paddle-ocr-gpu",):
        if "paddlepaddle-gpu" in local_map:
            return [
                [*py, "install", local_map["paddlepaddle-gpu"]["path"], "--no-index"],
                _pip_install(["paddleocr==3.7.0", "pillow==12.2.0"], find_links_dirs=find_dirs, mirror=mirror),
            ]
        # paddlepaddle-gpu 只在 Paddle 官方 cu126 索引提供（PyPI/镜像无 GPU whl；
        # 旧阿里云直链已失效，返回 404 HTML 会被 pip 当 wheel 解析失败）。
        # 清华源为主、官方源为补充，与 CPU 路径一致地装齐三件套。
        return [
            _pip_install(
                [
                    "paddlepaddle-gpu==3.3.1",
                    "paddleocr==3.7.0",
                    "pillow==12.2.0",
                    "--extra-index-url", _PADDLE_GPU_INDEX,
                ],
                find_links_dirs=find_dirs,
                mirror=mirror,
            ),
        ]

    if path in ("paddle-ocr-cpu", "ocr-cpu", "ocr", "cpu"):
        # pillow 必须锁版本：与 pyproject[ocr] / requirements-ocr.txt / 安装器
        # install_optional.py 三处口径一致，避免 GUI 路径自由解析漂移。
        return [
            _pip_install(
                ["paddlepaddle==3.3.1", "paddleocr==3.7.0", "pillow==12.2.0"],
                find_links_dirs=find_dirs,
                mirror=mirror,
            ),
        ]

    return None


# 各安装路径的磁盘空间预估（wheel 下载缓存 + 解压安装双份占用，留足余量）
_DISK_SPACE_NEEDS_GB: dict[str, float] = {
    "cuda12": 4.0,
    "cuda13": 3.0,
    "directml": 1.0,
    "paddle-ocr-gpu": 6.0,
    "ocr-cpu": 2.5,
    "cpu": 2.5,
}


def _check_disk_space(path: str) -> str | None:
    """安装前预检磁盘剩余空间，不足时返回错误文案。

    检查两处：解释器所在盘（安装落盘）与用户主目录所在盘
    （pip 下载缓存与 %TEMP% 默认在此）。空间不足时 pip 只会抛
    ``[Errno 28] No space left on device``，对桌面用户如同天书，必须前置拦截。
    """
    import shutil
    import tempfile

    needed = _DISK_SPACE_NEEDS_GB.get(path, 2.0)
    roots: list[str] = []
    for p in (sys.prefix, tempfile.gettempdir()):
        root = str(Path(p).anchor or "/")
        if root not in roots:
            roots.append(root)
    for root in roots:
        free_gb = shutil.disk_usage(root).free / (1 << 30)
        if free_gb < needed:
            return (
                f"磁盘空间不足：该方案约需 {needed:.0f}GB，"
                f"而 {root} 盘仅剩 {free_gb:.1f}GB。"
                "请清理磁盘（可运行 pip cache purge 清理下载缓存）后重试。"
            )
    return None


# 安装超时：pip 单条命令读输出的空闲上限与总上限（秒）。
# 没有超时的话 pip 网络挂死会让 SSE 永不终帧，前端永久卡「正在安装」。
_INSTALL_IDLE_TIMEOUT_SEC = 300.0
_INSTALL_TOTAL_TIMEOUT_SEC = 3600.0

# 失败原因分类关键字（小写匹配；pip 中文本地化输出经 utf-8 replace 解码后
# 英文关键字仍保留，中文关键字在 GBK 环境可能乱码，仅作补充）
_DISK_KEYWORDS = ("no space left", "errno 28")
_FILE_LOCK_KEYWORDS = (
    "winerror 5",
    "access is denied",
    "permissionerror",
    "cannot access the file",
    "being used by another process",
    "拒绝访问",
)
_NETWORK_KEYWORDS = (
    "connectionerror",
    "readtimeout",
    "connection timed out",
    "timed out",
    "unreachable",
    "connection reset",
    "failed to resolve",
    "ssl",
    "proxy error",
)


def _classify_install_failure(lines: list[str]) -> str:
    """按输出尾部归类失败原因：disk / filelock / network / generic。"""
    blob = "\n".join(lines).lower()
    if any(k in blob for k in _DISK_KEYWORDS):
        return "disk"
    if any(k in blob for k in _FILE_LOCK_KEYWORDS):
        return "filelock"
    if any(k in blob for k in _NETWORK_KEYWORDS):
        return "network"
    return "generic"


_FAILURE_MESSAGES: dict[str, str] = {
    "filelock": (
        "安装失败：文件被占用或无写入权限（Windows 错误 WinError 5）。\n"
        "两种常见原因：\n"
        "1. 后端服务正在运行，占用了 numpy/paddle 等运行库 —— "
        "请从客户端设置页重新安装（客户端会先停止后端再独立安装）；\n"
        "2. DocMind 安装在无写入权限的目录（如 C:\\Program Files）—— "
        "pip 无法向虚拟环境写文件。请以管理员身份运行客户端，"
        "或把 DocMind 安装到用户目录（默认 %LOCALAPPDATA%\\Programs，无需管理员）。"
    ),
    "generic": "常见原因：网络不可达、包冲突或 wheel 不兼容。"
    "若日志中有文件占用（WinError 5）字样，请先停止后端服务再重试。",
}


async def _run_install_commands(
    build_cmds, path: str, label: str, mirrors: list[str] | None = None
) -> AsyncGenerator[dict[str, Any], None]:
    """按镜像序列执行安装命令并流式产出事件（GPU / OCR 安装共用）。

    具备：Windows 文件锁专项识别、磁盘不足专项识别、网络类失败自动切换
    下一镜像整序列重试、空闲/总超时（超时终止子进程）。

    Args:
        build_cmds: ``mirror -> list[cmd] | None`` 工厂，供镜像回退重建命令序列。
    """
    if mirrors is None:
        configured = os.environ.get("PIP_INDEX_URL")
        mirror_list = [configured] if configured else list(_PIP_MIRRORS)
    else:
        mirror_list = list(mirrors)
    if not mirror_list:
        mirror_list = [_DEFAULT_MIRROR]

    if build_cmds(mirror_list[0]) is None:
        yield {"type": "error", "message": f"未知的安装路径: {path}"}
        return

    space_err = _check_disk_space(path)
    if space_err:
        yield {"type": "error", "message": space_err}
        return

    import subprocess

    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
        "creationflags": (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    }

    network_only_failure = False
    for attempt, mirror in enumerate(mirror_list):
        cmds = build_cmds(mirror)
        if not cmds:
            continue
        if attempt > 0:
            yield {
                "type": "log",
                "line": f"[提示] 网络异常，切换镜像重试（{attempt + 1}/{len(mirror_list)}）: {mirror}",
            }

        sequence_failed = False
        for cmd in cmds:
            cmd_str = " ".join(cmd)
            is_uninstall = len(cmd) >= 4 and cmd[1:3] == ["-m", "pip"] and cmd[3] == "uninstall"
            yield {"type": "log", "line": "$ " + cmd_str}
            logger.info("执行 %s 安装命令: %s", label, cmd_str)

            proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
            assert proc.stdout is not None
            recent_lines: list[str] = []
            timed_out = False
            deadline = asyncio.get_event_loop().time() + _INSTALL_TOTAL_TIMEOUT_SEC
            try:
                while True:
                    idle_left = deadline - asyncio.get_event_loop().time()
                    if idle_left <= 0:
                        timed_out = True
                        break
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=min(_INSTALL_IDLE_TIMEOUT_SEC, idle_left)
                    )
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if text:
                        recent_lines.append(text)
                        if len(recent_lines) > 15:
                            recent_lines.pop(0)
                        yield {"type": "log", "line": text}
            except asyncio.TimeoutError:
                timed_out = True

            if timed_out:
                logger.error("%s 安装超时（%s）: %s", label, "总时长" if recent_lines else "空闲", cmd_str)
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                yield {
                    "type": "error",
                    "message": (
                        f"{label}安装超时已终止（pip 长时间无输出或总时长超上限）。"
                        "多为网络卡住所致，请检查网络/代理后重试。"
                    ),
                }
                return

            rc = await proc.wait()
            if rc != 0:
                if is_uninstall:
                    warn_msg = "卸载前代组件被系统占用锁定，已跳过卸载并继续尝试覆盖安装..."
                    logger.warning("%s 卸载步骤跳过（退出码 %d）: %s", label, rc, warn_msg)
                    yield {"type": "log", "line": f"[提示] {warn_msg}"}
                    continue

                err_details = "\n".join(recent_lines[-5:]) if recent_lines else f"退出码 {rc}"
                kind = _classify_install_failure(recent_lines)
                logger.error("%s 安装步骤失败（退出码 %d，类别 %s）: %s\n详细输出:\n%s",
                             label, rc, kind, cmd_str, err_details)
                if kind == "disk":
                    yield {
                        "type": "error",
                        "message": "磁盘空间不足导致安装失败，请清理磁盘"
                        "（可运行 pip cache purge 清理 pip 下载缓存）后重试。",
                    }
                    return
                if kind == "filelock":
                    yield {"type": "error", "message": _FAILURE_MESSAGES["filelock"]}
                    return
                if kind == "network" and attempt < len(mirror_list) - 1:
                    sequence_failed = True
                    network_only_failure = True
                    break
                yield {
                    "type": "error",
                    "message": f"{label}安装失败（退出码 {rc}）：\n{err_details}\n{_FAILURE_MESSAGES['generic']}",
                }
                return
            logger.info("%s 安装步骤完成: %s", label, " ".join(cmd[:4]))

        if sequence_failed:
            continue
        yield {"type": "done", "success": True, "path": path}
        return

    # 所有镜像均因网络类失败耗尽
    if network_only_failure:
        yield {
            "type": "error",
            "message": f"{label}安装失败：所有镜像（清华/阿里云/PyPI）均网络失败，"
            "请检查网络连接或代理设置后重试。",
        }
    else:
        yield {"type": "error", "message": f"{label}安装失败（未知原因），请查看安装日志。"}


async def install_gpu_packages(
    path: str, mirrors: list[str] | None = None
) -> AsyncGenerator[dict[str, Any], None]:
    """按路径执行 GPU 加速包安装，流式产出事件字典。"""
    async for event in _run_install_commands(
        lambda m: _build_install_commands(path, m), path, "GPU", mirrors
    ):
        yield event


async def install_ocr_packages(
    path: str = "cpu", mirrors: list[str] | None = None, force: bool = False
) -> AsyncGenerator[dict[str, Any], None]:
    """按路径执行 OCR 依赖安装，流式产出事件字典。

    幂等保护：检测到 paddle + paddleocr 均已安装时跳过重复安装（重装会
    再次触发 pip 隐式替换 numpy → Windows 文件占用失败）。force=True
    （CLI --force 或请求体 force 字段）可强制重装修复。
    版本检测走 importlib.metadata，不 import paddle（避免在安装进程里
    提前加载 numpy DLL，否则后续 pip 替换 numpy 又会踩文件锁）。
    """
    if not force and path in ("paddle-ocr-cpu", "ocr-cpu", "ocr", "cpu"):
        installed = _dist_versions(("paddlepaddle", "paddlepaddle-gpu", "paddleocr"))
        if installed.get("paddleocr") and (installed.get("paddlepaddle") or installed.get("paddlepaddle-gpu")):
            engine = "paddlepaddle-gpu" if installed.get("paddlepaddle-gpu") else "paddlepaddle"
            yield {
                "type": "log",
                "line": (
                    f"[提示] OCR 组件已安装（{engine} {installed.get('paddlepaddle') or installed.get('paddlepaddle-gpu')}"
                    f" / paddleocr {installed['paddleocr']}），跳过重复安装。"
                    f"如需强制重装修复，请运行：python -m doc2mind.install_cli {path} --force"
                ),
            }
            yield {"type": "done", "success": True, "path": path}
            return
    async for event in _run_install_commands(
        lambda m: _build_install_commands(path, m), path, "OCR", mirrors
    ):
        yield event


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
