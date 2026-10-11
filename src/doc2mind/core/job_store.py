"""Backend-neutral job status storage and its in-process adapter."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, MutableMapping
from datetime import datetime, timezone
from typing import Any, Protocol, TypeVar, runtime_checkable

JobT = TypeVar("JobT")


@runtime_checkable
class JobStore(Protocol[JobT]):
    """Persistence seam for asynchronous job status records.

    Individual operations are thread-safe; multi-step state transitions remain
    caller-owned so existing HTTP locking and event semantics stay unchanged.
    """

    def __getitem__(self, job_id: str) -> JobT: ...

    def __iter__(self) -> Iterator[str]: ...

    def __len__(self) -> int: ...

    def __setitem__(self, job_id: str, job: JobT) -> None: ...

    def get(self, job_id: str, default: Any = None) -> JobT | Any: ...

    def pop(self, job_id: str, default: Any = None) -> JobT | Any: ...

    def items(self) -> list[tuple[str, JobT]]: ...

    def prune(
        self,
        *,
        terminal_statuses: tuple[str, ...],
        ttl_seconds: int,
        max_keep: int,
    ) -> list[str]: ...


class InMemoryJobStore(MutableMapping[str, JobT]):
    """Thread-safe process-local job store used by Lite deployments."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobT] = {}
        self._lock = threading.RLock()

    def __getitem__(self, job_id: str) -> JobT:
        with self._lock:
            return self._jobs[job_id]

    def __setitem__(self, job_id: str, job: JobT) -> None:
        with self._lock:
            self._jobs[job_id] = job

    def __delitem__(self, job_id: str) -> None:
        with self._lock:
            del self._jobs[job_id]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(tuple(self._jobs))

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)

    def get(self, job_id: str, default: Any = None) -> JobT | Any:
        with self._lock:
            return self._jobs.get(job_id, default)

    def pop(self, job_id: str, default: Any = None) -> JobT | Any:
        with self._lock:
            return self._jobs.pop(job_id, default)

    def items(self) -> list[tuple[str, JobT]]:
        with self._lock:
            return list(self._jobs.items())

    def prune(
        self,
        *,
        terminal_statuses: tuple[str, ...],
        ttl_seconds: int,
        max_keep: int,
    ) -> list[str]:
        """Remove expired terminal jobs and then the oldest excess terminals."""

        with self._lock:
            now = time.time()
            terminal = [
                (job_id, job, self._timestamp(job))
                for job_id, job in self._jobs.items()
                if getattr(job, "status", None) in terminal_statuses
            ]
            removed = {
                job_id
                for job_id, _, timestamp in terminal
                if now - timestamp > ttl_seconds
            }
            excess = max(0, len(self._jobs) - len(removed) - max_keep)
            if excess:
                oldest = sorted(
                    (item for item in terminal if item[0] not in removed),
                    key=lambda item: item[2],
                )
                removed.update(item[0] for item in oldest[:excess])
            for job_id in removed:
                self._jobs.pop(job_id, None)
            return list(removed)

    @staticmethod
    def _timestamp(job: Any) -> float:
        value = getattr(job, "finished_at", None) or getattr(job, "started_at", None)
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (TypeError, ValueError, OverflowError):
            return time.time()
