from __future__ import annotations

import anyio
from collections.abc import Awaitable, Callable

from gpu_arbiter.queue.models import RetriableError, Task, TaskStatus
from gpu_arbiter.queue.store import TaskStore


class QueueWorker:
    def __init__(
        self,
        store: TaskStore,
        execute_fn: Callable[[Task], Awaitable[tuple[int, bytes, dict]]] | None,
        poll_interval: float = 1.0,
    ) -> None:
        self._store = store
        self._execute_fn = execute_fn
        self._poll_interval = poll_interval
        self._running = False

    async def run_once(self) -> bool:
        task = await self._store.claim_next()
        if task is None:
            return False
        try:
            status_code, body, headers = await self._execute_fn(task)
            await self._store.update(
                task.task_id,
                status=TaskStatus.DONE,
                result_status=status_code,
                result_body=body,
                result_headers=headers,
            )
        except RetriableError:
            await self._store.update(task.task_id, status=TaskStatus.PENDING)
        except Exception as exc:
            await self._store.update(task.task_id, status=TaskStatus.FAILED, error=str(exc))
        return True

    async def run(self) -> None:
        self._running = True
        while self._running:
            processed = await self.run_once()
            if not processed:
                await anyio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False
