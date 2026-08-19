"""文件系统监控自动摄入模块。

基于 watchdog 监听本地目录中的新增或修改文件，
经过扩展名白名单过滤与时间窗口防抖后，自动触发 pipeline.ingest_path，
并通过回调对外广播入库完成事件。
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from doc2mind.core.config import Settings
from doc2mind.core.loader.detect import is_supported
from doc2mind.core.pipeline import ingest_path

logger = logging.getLogger("doc2mind.file_watcher")


class FileWatcher:
    """watchdog 目录监控：文件新增/修改 → 去抖 → 自动入库。"""

    def __init__(
        self,
        paths: list[str],
        settings: Settings,
        collection: str = "default",
        debounce_seconds: float = 5.0,
        on_ingested: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._paths = [str(Path(p).expanduser().resolve()) for p in paths if p and str(p).strip()]
        self._settings = settings
        self._collection = collection
        self._debounce_seconds = debounce_seconds
        self._on_ingested = on_ingested

        self._observer: Any = None
        self._is_running = False
        self._lock = threading.Lock()
        self._pending_timers: dict[str, threading.Timer] = {}

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        """启动监控线程。watchdog 未安装时记录日志降级，不抛异常。"""
        if self._is_running or not self._paths:
            return

        try:
            from watchdog.events import FileSystemEvent, FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning(
                "未安装 watchdog 依赖，文件监控自动摄入未启用；"
                "如需启用请运行：pip install doc2mind[serve] 或 pip install watchdog"
            )
            return

        class _Handler(FileSystemEventHandler):
            def __init__(self, outer: FileWatcher) -> None:
                self.outer = outer

            def on_created(self, event: FileSystemEvent) -> None:
                if not event.is_directory:
                    self.outer._schedule_ingest(event.src_path)

            def on_modified(self, event: FileSystemEvent) -> None:
                if not event.is_directory:
                    self.outer._schedule_ingest(event.src_path)

        handler = _Handler(self)
        self._observer = Observer()

        watched_count = 0
        for p_str in self._paths:
            p = Path(p_str)
            if not p.is_dir():
                logger.warning("监控路径不是有效目录，跳过: %s", p_str)
                continue
            try:
                self._observer.schedule(handler, str(p), recursive=True)
                watched_count += 1
                logger.info("已注册监控目录: %s (collection=%s)", p, self._collection)
            except Exception as e:  # noqa: BLE001
                logger.warning("注册监控目录失败（%s）: %s", p, e)

        if watched_count > 0:
            try:
                self._observer.start()
                self._is_running = True
                logger.info("FileWatcher 监控已启动 (共 %d 个目录)", watched_count)
            except Exception as e:  # noqa: BLE001
                logger.error("启动 watchdog Observer 失败: %s", e)
                self._observer = None

    def stop(self) -> None:
        """停止监控并取消所有挂起的定时器。幂等。"""
        with self._lock:
            # 取消尚未触发的定时器
            for timer in self._pending_timers.values():
                timer.cancel()
            self._pending_timers.clear()

        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3.0)
            except Exception as e:  # noqa: BLE001
                logger.warning("停止 watchdog Observer 异常: %s", e)
            finally:
                self._observer = None

        self._is_running = False
        logger.info("FileWatcher 监控已停止")

    def _schedule_ingest(self, file_path_str: str) -> None:
        """防抖调度：收到文件事件后重置定时器。"""
        p = Path(file_path_str)

        # 过滤临时文件与隐藏文件
        name = p.name
        if name.startswith((".", "~$", "#")) or name.endswith((".tmp", ".crdownload", ".part")):
            return

        if not is_supported(p):
            return

        norm_path = str(p.resolve())

        with self._lock:
            if norm_path in self._pending_timers:
                self._pending_timers[norm_path].cancel()

            timer = threading.Timer(
                self._debounce_seconds,
                self._execute_ingest,
                args=(norm_path,),
            )
            timer.daemon = True
            self._pending_timers[norm_path] = timer
            timer.start()

    def _execute_ingest(self, norm_path: str) -> None:
        """定时器到期后在后台线程执行入库。"""
        with self._lock:
            self._pending_timers.pop(norm_path, None)

        p = Path(norm_path)
        if not p.is_file():
            return

        logger.info("文件变更触发自动摄入: %s", p)
        try:
            resp = ingest_path(
                p,
                settings=self._settings,
                collection=self._collection,
                recursive=False,
            )
            status = "skipped"
            doc_id = None
            error = None

            if resp.ingested:
                item = resp.ingested[0]
                status = item.status
                doc_id = item.document_id
                error = item.error
            elif resp.failed > 0:
                status = "failed"
                error = "摄入失败"

            payload = {
                "path": str(p),
                "collection": self._collection,
                "result": status,
                "document_id": doc_id,
                "error": error,
            }

            if self._on_ingested is not None:
                try:
                    self._on_ingested(payload)
                except Exception as cb_err:  # noqa: BLE001
                    logger.warning("on_ingested 回调异常: %s", cb_err)

        except Exception as e:  # noqa: BLE001 — 单文件异常不影响 watcher 线程
            logger.warning("自动摄入文件异常（%s）: %s", p, e)
            if self._on_ingested is not None:
                with contextlib.suppress(Exception):
                    self._on_ingested({
                        "path": str(p),
                        "collection": self._collection,
                        "result": "failed",
                        "error": str(e),
                        "document_id": None,
                    })
