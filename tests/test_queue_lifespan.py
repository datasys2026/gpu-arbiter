from __future__ import annotations

import asyncio
import time
import uuid

import anyio
import pytest
from fastapi.testclient import TestClient

from gpu_arbiter.app import _cleanup_loop, create_app
from gpu_arbiter.config import ArbiterConfig
from gpu_arbiter.queue.models import Task, TaskStatus
from gpu_arbiter.queue.store import InMemoryTaskStore
from gpu_arbiter.queue.worker import QueueWorker
from gpu_arbiter.vram import StaticVRAMProbe


def make_pending_task(tenant_id: str = "t1", model_id: str = "local/chat") -> Task:
    return Task(
        task_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        model_id=model_id,
        route="/v1/chat/completions",
        method="POST",
        headers={},
        body=b"{}",
        status=TaskStatus.PENDING,
        created_at=0.0,
    )


def _make_config() -> ArbiterConfig:
    return ArbiterConfig.model_validate({
        "gpu": {"index": 0},
        "models": {
            "local/chat": {
                "route": "/v1/chat/completions",
                "upstream": "http://upstream:8003",
                "uses_gpu": True,
                "required_vram_mb": 0,
            }
        },
    })


# --- QueueWorker background task ---

async def test_worker_processes_task_when_running():
    store = InMemoryTaskStore()
    task = make_pending_task()
    await store.create(task)

    async def fast_execute(t: Task) -> tuple[int, bytes, dict]:
        return 200, b'{"ok":true}', {}

    worker = QueueWorker(store=store, execute_fn=fast_execute, poll_interval=0.01)

    async with anyio.create_task_group() as tg:
        tg.start_soon(worker.run)
        for _ in range(20):
            await anyio.sleep(0.02)
            if (await store.get(task.task_id)).status == TaskStatus.DONE:
                break
        tg.cancel_scope.cancel()

    assert (await store.get(task.task_id)).status == TaskStatus.DONE


# --- Cleanup loop ---

async def test_cleanup_loop_deletes_expired_tasks():
    store = InMemoryTaskStore()
    task = make_pending_task()
    await store.create(task)
    await store.update(task.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"")
    (await store.get(task.task_id)).completed_at = 0.0  # force to the past

    async with anyio.create_task_group() as tg:
        tg.start_soon(_cleanup_loop, store, 1.0, 0.01)
        await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    assert await store.get(task.task_id) is None


async def test_cleanup_loop_keeps_pending_tasks():
    store = InMemoryTaskStore()
    task = make_pending_task()
    await store.create(task)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_cleanup_loop, store, 0.0, 0.01)
        await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    assert await store.get(task.task_id) is not None


# --- Full lifespan integration via TestClient (sync) ---

def test_lifespan_wires_worker_processes_queued_task():
    store = InMemoryTaskStore()

    async def fast_execute(task: Task) -> tuple[int, bytes, dict]:
        return 200, b'{"ok":true}', {"content-type": "application/json"}

    app = create_app(
        _make_config(),
        vram_probe=StaticVRAMProbe(free_mb=99999),
        task_store=store,
        task_execute_fn=fast_execute,
        worker_poll_interval=0.05,
    )

    with TestClient(app) as client:
        resp = client.post(
            "/queue",
            json={"model": "local/chat"},
            headers={"X-Tenant-ID": "t1"},
        )
        assert resp.status_code == 202
        task_id = resp.json()["task_id"]

        final_status = "pending"
        for _ in range(30):
            time.sleep(0.05)
            poll = client.get(f"/tasks/{task_id}", headers={"X-Tenant-ID": "t1"})
            final_status = poll.json()["status"]
            if final_status == "done":
                break

        assert final_status == "done"
        result = client.get(f"/tasks/{task_id}", headers={"X-Tenant-ID": "t1"}).json()["result"]
        assert result["status_code"] == 200


# --- SQLiteTaskStore lifespan integration ---

def test_lifespan_initializes_and_closes_sqlite_store(tmp_path):
    from gpu_arbiter.queue.sqlite_store import SQLiteTaskStore

    store = SQLiteTaskStore(str(tmp_path / "queue.db"))

    async def fast_execute(task: Task) -> tuple[int, bytes, dict]:
        return 200, b'{"ok":true}', {}

    app = create_app(
        _make_config(),
        vram_probe=StaticVRAMProbe(free_mb=99999),
        task_store=store,
        task_execute_fn=fast_execute,
        worker_poll_interval=0.05,
    )

    with TestClient(app) as client:
        resp = client.post(
            "/queue",
            json={"model": "local/chat"},
            headers={"X-Tenant-ID": "t1"},
        )
        assert resp.status_code == 202
        task_id = resp.json()["task_id"]

        for _ in range(30):
            time.sleep(0.05)
            if client.get(f"/tasks/{task_id}", headers={"X-Tenant-ID": "t1"}).json()["status"] == "done":
                break

        assert client.get(f"/tasks/{task_id}", headers={"X-Tenant-ID": "t1"}).json()["status"] == "done"
