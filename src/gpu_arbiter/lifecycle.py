from __future__ import annotations

import httpx

from gpu_arbiter.config import HookConfig


class LifecycleRunner:
    def run_hook(self, hook: HookConfig | None) -> None:
        if hook is None:
            return
        if hook.type != "http":
            raise ValueError(f"unsupported hook type: {hook.type}")
        with httpx.Client(timeout=hook.timeout_seconds) as client:
            response = client.request(hook.method, hook.url)
            response.raise_for_status()
