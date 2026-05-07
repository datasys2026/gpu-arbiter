from __future__ import annotations

import time
from collections.abc import Callable


class InsufficientVRAMError(RuntimeError):
    def __init__(self, free_mb: int, required_mb: int) -> None:
        super().__init__("insufficient VRAM")
        self.free_mb = free_mb
        self.required_mb = required_mb


class StaticVRAMProbe:
    def __init__(self, free_mb: int) -> None:
        self._free_mb = free_mb

    def get_free_mb(self) -> int:
        return self._free_mb

    def ensure_available(self, required_mb: int) -> None:
        free_mb = self.get_free_mb()
        if free_mb < required_mb:
            raise InsufficientVRAMError(free_mb=free_mb, required_mb=required_mb)


class NVMLVRAMProbe:
    def __init__(self, gpu_index: int = 0) -> None:
        try:
            import pynvml
        except ImportError as exc:
            raise RuntimeError("Install gpu-arbiter[nvml] to enable NVML VRAM checks") from exc
        self._pynvml = pynvml
        self._pynvml.nvmlInit()
        self._handle = self._pynvml.nvmlDeviceGetHandleByIndex(gpu_index)

    def get_free_mb(self) -> int:
        info = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        return int(info.free // (1024 * 1024))

    def ensure_available(self, required_mb: int) -> None:
        free_mb = self.get_free_mb()
        if free_mb < required_mb:
            raise InsufficientVRAMError(free_mb=free_mb, required_mb=required_mb)

    def close(self) -> None:
        self._pynvml.nvmlShutdown()


def wait_for_vram_available(
    probe: StaticVRAMProbe,
    required_mb: int,
    timeout_seconds: float = 60,
    poll_interval: float = 2,
    sleep_fn: Callable[[float], None] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> None:
    sleep = sleep_fn if sleep_fn is not None else time.sleep
    now = now_fn if now_fn is not None else time.monotonic
    deadline = now() + timeout_seconds
    while True:
        free_mb = probe.get_free_mb()
        if free_mb >= required_mb:
            return
        if now() >= deadline:
            raise InsufficientVRAMError(free_mb=free_mb, required_mb=required_mb)
        sleep(poll_interval)
