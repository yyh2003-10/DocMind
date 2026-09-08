"""独立进程插件安装 CLI（python -m doc2mind.install_cli <path> [--force]）。

为什么必须独立进程：后端服务进程运行时已加载 numpy / onnxruntime / paddle
等运行库 DLL，Windows 不允许删除/替换被映射的 DLL 文件——在后端进程内拉起
pip 安装 paddlepaddle（需要替换 numpy）必然报 WinError 5「拒绝访问」。
本 CLI 由客户端在**停止后端后**调用，进程内不导入任何重依赖
（system_env 已改为懒加载 fastembed），pip 可安全替换全部文件。

用法：
  python -m doc2mind.install_cli ocr-cpu            # OCR CPU 三件套
  python -m doc2mind.install_cli cuda12             # GPU 加速嵌入
  python -m doc2mind.install_cli ocr-cpu --force    # 强制重装（跳过幂等检查）

输出：逐行纯文本日志（客户端直接回显到安装日志框）；退出码 0=成功 1=失败 2=参数错误。
同时把安装状态写入 %LOCALAPPDATA%\\doc2mind\\plugin_install.json，
供 doctor 体检识别「上次安装中断」。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

# 支持的安装路径（与 system_env._build_install_commands 保持一致）
OCR_PATHS = {"paddle-ocr-cpu", "ocr-cpu", "ocr", "cpu"}
GPU_PATHS = {"cuda12", "cu12", "cuda13", "cu13", "directml", "paddle-ocr-gpu"}


def _state_file() -> Path:
    from doc2mind.core.config import _user_data_dir

    return _user_data_dir() / "plugin_install.json"


def _write_state(status: str, path: str) -> None:
    """写安装状态标记（失败不影响安装流程本身）。"""
    try:
        data: dict[str, Any] = {"status": status, "path": path, "ts": time.time()}
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def read_install_state() -> dict[str, Any] | None:
    """读取上次安装状态标记（doctor 体检用）；文件缺失/损坏返回 None。"""
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:
        return None


async def _run(path: str, force: bool) -> int:
    from doc2mind.core.system_env import install_gpu_packages, install_ocr_packages

    if path in OCR_PATHS:
        gen = install_ocr_packages(path, force=force)
    elif path in GPU_PATHS:
        gen = install_gpu_packages(path)
    else:
        print(f"[错误] 未知的安装路径: {path}（可选: {', '.join(sorted(OCR_PATHS | GPU_PATHS))}）")
        return 2

    async for event in gen:
        etype = event.get("type")
        if etype == "log":
            print(event.get("line", ""), flush=True)
        elif etype == "error":
            print(f"[错误] {event.get('message', '')}", flush=True)
            return 1
        elif etype == "done":
            print(f"[完成] 安装成功（路径: {event.get('path', path)}）", flush=True)
            return 0
    print("[错误] 安装流程异常终止（未收到终帧）")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DocMind 插件独立安装器（须先停止后端服务）")
    parser.add_argument("path", help="安装路径，如 ocr-cpu / cuda12 / directml")
    parser.add_argument("--force", action="store_true", help="跳过已安装检查，强制重装")
    args = parser.parse_args(argv)

    # 关键：Windows 下 stdout 默认 GBK，而 pip 的 GBK 输出经 UTF-8 解码后
    # 会含替换符 U+FFFD，直接 print 回显会抛 UnicodeEncodeError 让安装中途
    # 崩溃。强制 UTF-8 + 替换降级（C# 端 PluginInstallService 按 UTF-8 解码）。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — 非 TextIO 流（测试替身等）跳过
            pass

    _write_state("running", args.path)
    try:
        rc = asyncio.run(_run(args.path, args.force))
    except KeyboardInterrupt:
        print("[错误] 安装被用户中断")
        rc = 1
    except Exception as e:  # noqa: BLE001 — 顶层兜底，任何异常都要有明确退出码
        print(f"[错误] 安装异常：{e}")
        rc = 1
    _write_state("ok" if rc == 0 else "failed", args.path)
    return rc


if __name__ == "__main__":
    sys.exit(main())
