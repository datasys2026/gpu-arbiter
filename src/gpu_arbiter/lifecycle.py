from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from gpu_arbiter.config import HealthConfig, HookConfig


class UpstreamNotReadyError(RuntimeError):
    def __init__(self, url: str, timeout: float) -> None:
        super().__init__(f"upstream {url!r} not ready after {timeout}s")
        self.url = url
        self.timeout = timeout


class LifecycleRunner:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("gpu_arbiter")

    def _log(self, event: str, **fields: object) -> None:
        payload = {"event": event, **fields}
        self.logger.info(json.dumps(payload, ensure_ascii=False, default=str))

    async def run_hook(self, hook: HookConfig | None) -> None:
        if hook is None:
            return
        if hook.type != "http":
            raise ValueError(f"unsupported hook type: {hook.type}")
        started_at = time.perf_counter()
        self._log("hook_start", method=hook.method, url=hook.url)
        async with httpx.AsyncClient(timeout=hook.timeout_seconds) as client:
            response = await client.request(
                hook.method,
                hook.url,
                headers=hook.headers,
                json=hook.body_json,
            )
            response.raise_for_status()
        self._log(
            "hook_success",
            method=hook.method,
            url=hook.url,
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )

    async def wait_until_ready(
        self,
        health: HealthConfig | None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        if health is None:
            return
        import anyio

        sleep = sleep_fn if sleep_fn is not None else anyio.sleep
        now = now_fn if now_fn is not None else time.monotonic
        deadline = now() + health.wait_timeout_seconds
        while True:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.request(health.method, health.url)
                    if resp.is_success:
                        self._log("health_poll_ok", url=health.url)
                        return
                self._log("health_poll_fail", url=health.url, status_code=resp.status_code)
            except Exception as exc:
                self._log("health_poll_error", url=health.url, error=str(exc))
            if now() >= deadline:
                raise UpstreamNotReadyError(health.url, health.wait_timeout_seconds)
            await sleep(health.poll_interval_seconds)

    async def run_hooks(self, hooks: HookConfig | list[HookConfig] | None, *, ignore_errors: bool = False) -> None:
        if hooks is None:
            return
        if isinstance(hooks, HookConfig):
            try:
                await self.run_hook(hooks)
            except Exception as exc:
                self._log(
                    "hook_failure",
                    method=hooks.method,
                    url=hooks.url,
                    ignored=ignore_errors,
                    error=str(exc),
                )
                if not ignore_errors:
                    raise
            return
        for hook in hooks:
            try:
                await self.run_hook(hook)
            except Exception as exc:
                self._log(
                    "hook_failure",
                    method=hook.method,
                    url=hook.url,
                    ignored=ignore_errors,
                    error=str(exc),
                )
                if not ignore_errors:
                    raise

