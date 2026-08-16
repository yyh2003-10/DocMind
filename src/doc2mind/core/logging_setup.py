"""全局日志配置 — `doc2mind` 包级 logger + 文件轮转 + stderr。

日志文件位置（与数据库同数据目录）：
- Windows: %LOCALAPPDATA%\\doc2mind\\logs\\doc2mind.log
- Linux:   ~/.local/share/doc2mind/logs/doc2mind.log
- macOS:   ~/Library/Application Support/doc2mind/logs/doc2mind.log

`setup_logging()` 幂等，在 CLI app 回调与 server/MCP 启动时各调用一次。
只配置 `doc2mind` 命名空间（propagate=False），不触碰 root logger，
避免影响 uvicorn / fastembed 等第三方库自身的日志行为。

目录不可创建或文件不可写时退化为仅 stderr，不抛异常 —— 日志不应阻断业务。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from doc2mind.core.config import _user_data_dir

_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3
_configured = False


def log_file_path() -> Path:
    """日志文件路径（无论是否已配置/可写）。"""
    return _user_data_dir() / "logs" / "doc2mind.log"


def setup_logging(level: int | str | None = None, *, force: bool = False) -> Path | None:
    """配置 `doc2mind` 命名空间日志：文件轮转 + stderr。

    Args:
        level: 日志级别，默认取环境变量 `DOC2MIND_LOG_LEVEL`（缺省 INFO）。
        force: 已配置时强制重配（测试用）。

    Returns:
        日志文件路径；文件 handler 不可用时返回 None（仅 stderr）。
    """
    global _configured
    if _configured and not force:
        return log_file_path()
    if level is None:
        level = os.environ.get("DOC2MIND_LOG_LEVEL", "INFO")

    pkg_logger = logging.getLogger("doc2mind")
    pkg_logger.setLevel(level if isinstance(level, int) else str(level).upper())
    pkg_logger.propagate = False
    # 清掉旧 handler（force 重配时避免重复输出）
    for h in list(pkg_logger.handlers):
        pkg_logger.removeHandler(h)
        h.close()

    formatter = logging.Formatter(_FMT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.setLevel(logging.WARNING)  # 终端只出告警以上，常规信息落文件
    pkg_logger.addHandler(console)

    file_path: Path | None = None
    try:
        log_dir = log_file_path().parent
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_file_path(),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        fh.setFormatter(formatter)
        pkg_logger.addHandler(fh)
        file_path = log_file_path()
    except OSError as e:  # noqa: BLE001 — 目录只读/磁盘满等，退化仅 stderr
        pkg_logger.warning("日志文件不可用（%s），仅输出到 stderr", e)

    _configured = True
    return file_path
