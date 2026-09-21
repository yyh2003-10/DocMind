"""文件系统监控自动摄入模块。

基于 watchdog 监听本地目录中的新增或修改文件，
经过扩展名白名单过滤与时间窗口防抖后，自动触发 pipeline.ingest_path，
并通过回调对外广播入库完成事件。

并发模型（防抖合并 + 单 worker 串行）：
- 事件回调只把路径放进待处理集合并重置一个去抖定时器（旧实现是每文件
  一个定时器、各自起线程），防抖到期后把积压路径一次性交给 worker 队列；
- 单 worker 线程逐个摄入，配合可注入的全局写锁（http 装配时传
  state._write_lock），不再与 delete/reindex 产生并发写竞争；
- store/embedder 经 store_provider 复用 HTTP 服务的单例（工厂缓存保证
  embedder 全进程一份），不再每文件重建；
- 入库产生的待整理文档交给 run_background_curate 后台执行，worker
  不被 LLM 调用阻塞。
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from doc2mind.core.config import Settings
from doc2mind.core.loader.detect import is_supported
from doc2mind.core.pipeline import ingest_path, run_background_curate
from doc2mind.core.store.sqlite_vec import VectorStore

logger = logging.getLogger("doc2mind.file_watcher")


class FileWatcher:
    """watchdog 目录监控：文件新增/修改 → 去抖合并 → 串行自动入库。"""

    def __init__(
        self,
        paths: list[str],
        settings: Settings,
        collection: str = "default",
        debounce_seconds: float = 5.0,
        on_ingested: Callable[[dict[str, Any]], None] | None = None,
        store_provider: Callable[[], VectorStore | None] | None = None,
        write_lock: threading.Lock | None = None,
    ) -> None:
        self._paths = [str(Path(p).expanduser().resolve()) for p in paths if p and p.strip()]
        self._settings = settings
        self._collection = collection
        self._debounce_seconds = debounce_seconds
        self._on_ingested = on_ingested
        # 共享单例 store（HTTP 服务装配时注入；独立使用时为 None，
        # ingest_path / run_background_curate 自开自关）
        self._store_provider = store_provider
        self._write_lock = write_lock

        self._observer: Any = None
        self._is_running = False
        self._lock = threading.Lock()
        # 去抖合并：待处理路径集合 + 单个到点定时器
        self._pending: set[str] = set()
        self._flush_timer: threading.Timer | None = None
        # 单 worker 串行摄入
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        """启动监控线程。watchdog 未安装时记录日志降级，不抛异常。"""
        if self._is_running or not self._paths:
            return

        try:
            from watchdog.events import (  # type: ignore[import-untyped, import-not-found]
                FileSystemEvent,
                FileSystemEventHandler,
            )
            from watchdog.observers import (
                Observer,  # type: ignore[import-untyped, import-not-found]
            )
        except ImportError:
            logger.warning(
                "未安装 watchdog 依赖，文件监控自动摄入未启用；"
                "如需启用请运行：pip install 'doc2mind[server]' 或 pip install watchdog"
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
            self._worker = threading.Thread(
                target=self._worker_loop, daemon=True, name="file-watcher-worker"
            )
            self._worker.start()
            try:
                self._observer.start()
                self._is_running = True
                logger.info("FileWatcher 监控已启动 (共 %d 个目录)", watched_count)
            except Exception as e:  # noqa: BLE001
                logger.error("启动 watchdog Observer 失败: %s", e)
                self._observer = None
                self._queue.put(None)  # 停掉 worker

    def stop(self) -> None:
        """停止监控并取消挂起的定时器。幂等。"""
        with self._lock:
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            self._pending.clear()

        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3.0)
            except Exception as e:  # noqa: BLE001
                logger.warning("停止 watchdog Observer 异常: %s", e)
            finally:
                self._observer = None

        self._is_running = False
        self._queue.put(None)  # 哨兵：worker 消费完积压后退出
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None
        logger.info("FileWatcher 监控已停止")

    # --- 调度：去抖合并 ---

    def _schedule_ingest(self, file_path_str: str) -> None:
        """防抖调度：同一窗口内的变更合并进待处理集合，重置单个定时器。"""
        p = Path(file_path_str)

        # 过滤临时文件与隐藏文件
        name = p.name
        if name.startswith((".", "~$", "#")) or name.endswith((".tmp", ".crdownload", ".part")):
            return

        if not is_supported(p):
            return

        try:
            norm_path = str(p.resolve())
        except OSError:
            return

        with self._lock:
            self._pending.add(norm_path)
            self._ensure_worker_locked()
            if self._flush_timer is not None:
                self._flush_timer.cancel()
            self._flush_timer = threading.Timer(
                self._debounce_seconds, self._flush_pending
            )
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _ensure_worker_locked(self) -> None:
        """惰性启动 worker 线程（未调用 start() 的独立使用场景也能摄入）。"""
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._worker_loop, daemon=True, name="file-watcher-worker"
            )
            self._worker.start()

    def _flush_pending(self) -> None:
        """去抖到期：把积压路径一次性交给 worker 队列。"""
        with self._lock:
            batch = list(self._pending)
            self._pending.clear()
            self._flush_timer = None
        for path_str in batch:
            self._queue.put(path_str)

    # --- 执行：单 worker 串行摄入 ---

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # 停止哨兵
                break
            try:
                self._ingest_file(Path(item))
            except Exception as e:  # noqa: BLE001 — 单文件异常不影响 worker
                logger.warning("自动摄入文件异常（%s）: %s", item, e)

    def _shared_store(self) -> VectorStore | None:
        """取共享单例 store（未就绪返回 None，由 ingest_path 自行兜底）。"""
        if self._store_provider is None:
            return None
        try:
            return self._store_provider()
        except Exception:  # noqa: BLE001 — 单例未就绪时回退独立 store
            return None

    def _ingest_file(self, p: Path) -> None:
        if not p.is_file():
            return

        logger.info("文件变更触发自动摄入: %s", p)
        store = self._shared_store()

        def _do() -> Any:
            return ingest_path(
                p,
                settings=self._settings,
                collection=self._collection,
                recursive=False,
                store=store,
                cancel_event=None,
            )

        # 写互斥：与 HTTP 侧 ingest / delete / reindex 串行（历史实现
        # 旁路写锁，可与 reindex 的 DROP+回填并发写同一张表）
        if self._write_lock is not None:
            with self._write_lock:
                summary = _do()
        else:
            summary = _do()

        # 入库自动整理：导入热路径已剥离，交给后台线程执行（LLM 调用
        # 不阻塞 worker 处理下一个文件）
        if summary.curatable_document_ids:
            threading.Thread(
                target=run_background_curate,
                args=(self._settings, summary.curatable_document_ids, self._collection),
                kwargs={"store": store, "write_lock": self._write_lock},
                daemon=True,
                name="watch-curate",
            ).start()

        item = summary.results[0] if summary.results else None
        status = item.status if item is not None else (
            "failed" if summary.failed > 0 else "skipped"
        )
        payload = {
            "path": str(p),
            "collection": self._collection,
            "result": status,
            "document_id": item.document_id if item is not None else None,
            "error": item.error if item is not None else "摄入失败",
        }
        if self._on_ingested is not None:
            try:
                self._on_ingested(payload)
            except Exception as cb_err:  # noqa: BLE001
                logger.warning("on_ingested 回调异常: %s", cb_err)
