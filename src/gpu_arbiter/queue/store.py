from __future__ import annotations

from typing import Protocol, runtime_checkable

from gpu_arbiter.queue.models import Task, TaskStatus


@runtime_checkable
class TaskStore(Protocol):
    async def create(self, task: Task) -> None: ...
    async def get(self, task_id: str) -> Task | None: ...
    async def claim_next(self) -> Task | None: ...
    async def update(self, task_id: str, **fields: object) -> None: ...
    async def queue_depth(self, tenant_id: str) -> int: ...
    async def active_counts(self) -> dict: ...


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._last_tenant: str | None = None

    async def create(self, task: Task) -> None:
        self._tasks[task.task_id] = task

    async def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def claim_next(self) -> Task | None:
        pending = [t for t in self._tasks.values() if t.status == TaskStatus.PENDING]
        if not pending:
            return None

        tenants = sorted({t.tenant_id for t in pending})
        # rotate so the tenant after the last served one comes first
        if self._last_tenant in tenants:
            idx = tenants.index(self._last_tenant)
            tenants = tenants[idx + 1 :] + tenants[: idx + 1]

        for tenant in tenants:
            candidates = sorted(
                (t for t in pending if t.tenant_id == tenant),
                key=lambda t: t.created_at,
            )
            if candidates:
                chosen = candidates[0]
                chosen.status = TaskStatus.RUNNING
                self._last_tenant = tenant
                return chosen

        return None

    async def update(self, task_id: str, **fields: object) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        for key, value in fields.items():
            setattr(task, key, value)

    async def queue_depth(self, tenant_id: str) -> int:
        return sum(
            1
            for t in self._tasks.values()
            if t.tenant_id == tenant_id and t.status == TaskStatus.PENDING
        )

    async def active_counts(self) -> dict:
        all_tasks = list(self._tasks.values())
        pending = sum(1 for t in all_tasks if t.status == TaskStatus.PENDING)
        running = sum(1 for t in all_tasks if t.status == TaskStatus.RUNNING)
        tenants = sorted(
            {t.tenant_id for t in all_tasks if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)}
        )
        return {"pending": pending, "running": running, "tenants": tenants}
