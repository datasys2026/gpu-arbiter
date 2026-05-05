from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from gpu_arbiter.config import HookConfig


class LifecycleRunner:
    def __init__(self, poll_interval_seconds: float = 1, logger: logging.Logger | None = None) -> None:
        self.poll_interval_seconds = poll_interval_seconds
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

    async def wait_for_health(self, hook: HookConfig | None) -> None:
        if hook is None:
            return
        if hook.type != "http":
            raise ValueError(f"unsupported hook type: {hook.type}")

        deadline = time.monotonic() + hook.wait_timeout_seconds
        last_error: Exception | None = None
        started_at = time.perf_counter()
        self._log("health_wait_start", method=hook.method, url=hook.url, wait_timeout_seconds=hook.wait_timeout_seconds)
        async with httpx.AsyncClient(timeout=hook.timeout_seconds) as client:
            while time.monotonic() <= deadline:
                try:
                    response = await client.request(hook.method, hook.url, headers=hook.headers)
                    if 200 <= response.status_code < 400:
                        self._log(
                            "health_ready",
                            method=hook.method,
                            url=hook.url,
                            status_code=response.status_code,
                            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
                        )
                        return
                    if 400 <= response.status_code < 500:
                        raise httpx.HTTPStatusError(
                            f"health check fatal: {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    # 5xx — fall through to retry
                except httpx.HTTPStatusError:
                    raise
                except httpx.HTTPError as exc:
                    last_error = exc
                    self._log("health_poll_error", method=hook.method, url=hook.url, error=str(exc))
                if self.poll_interval_seconds:
                    await asyncio.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"health check timed out: {hook.url}") from last_error
