from __future__ import annotations

import uuid

import pytest

from gpu_arbiter.queue.models import Task, TaskStatus
from gpu_arbiter.queue.store import InMemoryTaskStore
from gpu_arbiter.queue.worker import QueueWorker


def make_task(tenant_id: str = "t1", model_id: str = "m1") -> Task:
    return Task(
        task_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        model_id=model_id,
        route="/v1/generate",
        method="POST",
        headers={"content-type": "application/json"},
        body=b'{"model": "m1"}',
        status=TaskStatus.PENDING,
        created_at=0.0,
    )


async def test_run_once_returns_false_when_no_tasks():
    store = InMemoryTaskStore()
    worker = QueueWorker(store=store, execute_fn=None)
    assert await worker.run_once() is False


async def test_run_once_executes_task_and_marks_done():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)

    async def fake_execute(t: Task) -> tuple[int, bytes, dict]:
        return 200, b'{"ok": true}', {"content-type": "application/json"}

    worker = QueueWorker(store=store, execute_fn=fake_execute)
    assert await worker.run_once() is True

    done = await store.get(task.task_id)
    assert done.status == TaskStatus.DONE
    assert done.result_status == 200
    assert done.result_body == b'{"ok": true}'
    assert done.result_headers == {"content-type": "application/json"}


async def test_run_once_marks_failed_on_execute_exception():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)

    async def failing_execute(t: Task) -> tuple[int, bytes, dict]:
        raise RuntimeError("GPU exploded")

    worker = QueueWorker(store=store, execute_fn=failing_execute)
    assert await worker.run_once() is True

    failed = await store.get(task.task_id)
    assert failed.status == TaskStatus.FAILED
    assert "GPU exploded" in failed.error


async def test_run_once_returns_true_and_processes_in_order():
    store = InMemoryTaskStore()
    processed: list[str] = []

    task1 = make_task(tenant_id="t1")
    task1.created_at = 0.0
    task2 = make_task(tenant_id="t1")
    task2.created_at = 1.0
    await store.create(task1)
    await store.create(task2)

    async def recording_execute(t: Task) -> tuple[int, bytes, dict]:
        processed.append(t.task_id)
        return 200, b"", {}

    worker = QueueWorker(store=store, execute_fn=recording_execute)
    await worker.run_once()
    await worker.run_once()

    assert processed == [task1.task_id, task2.task_id]
