from __future__ import annotations

import time

import httpx

from gpu_arbiter.config import HookConfig


class LifecycleRunner:
    def __init__(self, poll_interval_seconds: float = 1) -> None:
        self.poll_interval_seconds = poll_interval_seconds

    def run_hook(self, hook: HookConfig | None) -> None:
        if hook is None:
            return
        if hook.type != "http":
            raise ValueError(f"unsupported hook type: {hook.type}")
        with httpx.Client(timeout=hook.timeout_seconds) as client:
            response = client.request(
                hook.method,
                hook.url,
                headers=hook.headers,
                json=hook.body_json,
            )
            response.raise_for_status()

    def run_hooks(self, hooks: HookConfig | list[HookConfig] | None) -> None:
        if hooks is None:
            return
        if isinstance(hooks, HookConfig):
            self.run_hook(hooks)
            return
        for hook in hooks:
            self.run_hook(hook)

    def wait_for_health(self, hook: HookConfig | None) -> None:
        if hook is None:
            return
        if hook.type != "http":
            raise ValueError(f"unsupported hook type: {hook.type}")

        deadline = time.monotonic() + hook.wait_timeout_seconds
        last_error: Exception | None = None
        with httpx.Client(timeout=hook.timeout_seconds) as client:
            while time.monotonic() <= deadline:
                try:
                    response = client.request(hook.method, hook.url, headers=hook.headers)
                    if response.status_code < 500:
                        response.raise_for_status()
                        return
                except httpx.HTTPError as exc:
                    last_error = exc
                if self.poll_interval_seconds:
                    time.sleep(self.poll_interval_seconds)
        if last_error:
            raise last_error
        raise TimeoutError(f"health check timed out: {hook.url}")
