from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator


class GPUBusyError(RuntimeError):
    def __init__(self, holder: str | None) -> None:
        super().__init__("gpu is busy")
        self.holder = holder


class InMemoryGPULock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None
        self._acquired_at: float | None = None

    @property
    def holder(self) -> str | None:
        return self._holder

    @property
    def held_seconds(self) -> float | None:
        if self._acquired_at is None:
            return None
        return time.monotonic() - self._acquired_at

    @contextlib.contextmanager
    def acquire(self, holder: str) -> Iterator[None]:
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            raise GPUBusyError(self._holder)
        self._holder = holder
        self._acquired_at = time.monotonic()
        try:
            yield
        finally:
            self._holder = None
            self._acquired_at = None
            self._lock.release()
