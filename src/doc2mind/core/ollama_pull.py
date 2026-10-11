"""Ollama 模型拉取管理：子进程执行 `ollama pull`，解析 stdout 进度供前端轮询。

设计：
- 单飞：同一时刻只允许一个 pull 任务（模块级锁），重复调用返回当前进度。
- 进度解析：Ollama pull stdout 是 "\\r" 分隔的 JSON 行（status/completed/total），
  取最近一帧计算百分比；无法解析时退化为 status 文本。
- 不做静默安装：pull 必须由用户显式点击触发（C# 侧确认框保证）。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field

# 拉取状态机：idle → pulling → done / error
_status: dict[str, object] = {"state": "idle", "model": "", "percent": 0.0,
                              "message": "", "started_at": 0.0, "error": ""}
_proc: asyncio.subprocess.Process | None = None
_lock = asyncio.Lock()


def _ollama_exe() -> str:
    """定位 ollama 可执行文件；找不到时抛 RuntimeError。"""
    exe = shutil.which("ollama")
    if exe:
        return exe
    import os
    cand = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "Programs", "Ollama", "ollama.exe")
    if os.path.exists(cand):
        return cand
    raise RuntimeError("未找到 ollama 可执行文件，请先安装 Ollama")


@dataclass
class PullFrame:
    """一帧解析后的进度。"""

    status: str = ""
    completed: int = 0
    total: int = 0


def _parse_frame(raw: str) -> PullFrame:
    raw = raw.strip()
    if not raw.startswith("{"):
        return PullFrame(status=raw)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return PullFrame(status=raw[:120])
    return PullFrame(
        status=str(obj.get("status", "")),
        completed=int(obj.get("completed") or 0),
        total=int(obj.get("total") or 0),
    )


async def pull_status() -> dict[str, object]:
    """当前拉取状态快照（供 GET 轮询）。"""
    if _status["state"] == "pulling" and _proc is not None and _proc.returncode is not None:
        # 子进程已退出但状态未收尾（异常路径兜底）
        _status["state"] = "error" if _proc.returncode != 0 else "done"
    return dict(_status)


async def start_pull(model: str) -> dict[str, object]:
    """启动一次 ollama pull（单飞，重复调用返回现有进度）。"""
    global _proc
    async with _lock:
        if _status["state"] == "pulling":
            return dict(_status)
        try:
            exe = _ollama_exe()
        except RuntimeError as e:
            _status.update(state="error", model=model, error=str(e))
            return dict(_status)

        _status.update(state="pulling", model=model, percent=0.0,
                       message="启动拉取…", started_at=time.time(), error="")

        async def _run() -> None:
            global _proc
            try:
                _proc = await asyncio.create_subprocess_exec(
                    exe, "pull", model,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                assert _proc.stdout is not None
                buf = ""
                while True:
                    chunk = await _proc.stdout.read(64)
                    if not chunk:
                        break
                    buf += chunk.decode("utf-8", errors="replace")
                    while "\r" in buf or "\n" in buf:
                        for sep in ("\r", "\n"):
                            if sep in buf:
                                line, buf = buf.split(sep, 1)
                                break
                        frame = _parse_frame(line)
                        if frame.total > 0:
                            pct = round(frame.completed / frame.total * 100, 1)
                            _status["percent"] = min(pct, 100.0)
                        if frame.status:
                            _status["message"] = frame.status
                rc = await _proc.wait()
                if rc == 0:
                    _status.update(state="done", percent=100.0, message="拉取完成")
                else:
                    _status.update(state="error",
                                   message="拉取失败",
                                   error=_status["message"] or f"exit {rc}")
            except Exception as e:  # noqa: BLE001 —— 后台任务兜底，状态落 error
                _status.update(state="error", message="拉取异常", error=str(e))

        asyncio.get_running_loop().create_task(_run())
        return dict(_status)
