from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator


class GPUBusyError(RuntimeError):
    def __init__(self, holder: str | None) -> None:
        super().__init__("gpu is busy")
        self.holder = holder


class InMemoryGPULock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None

    @property
    def holder(self) -> str | None:
        return self._holder

    @contextlib.contextmanager
    def acquire(self, holder: str) -> Iterator[None]:
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            raise GPUBusyError(self._holder)
        self._holder = holder
        try:
            yield
        finally:
            self._holder = None
            self._lock.release()
