from __future__ import annotations


class InsufficientVRAMError(RuntimeError):
    def __init__(self, free_mb: int, required_mb: int) -> None:
        super().__init__("insufficient VRAM")
        self.free_mb = free_mb
        self.required_mb = required_mb


class StaticVRAMProbe:
    def __init__(self, free_mb: int) -> None:
        self.free_mb = free_mb

    def get_free_mb(self) -> int:
        return self.free_mb

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
