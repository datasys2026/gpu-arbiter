from __future__ import annotations

import uuid

import pytest

from gpu_arbiter.queue.models import Task, TaskStatus
from gpu_arbiter.queue.store import InMemoryTaskStore


def make_task(tenant_id: str = "t1", model_id: str = "m1", created_at: float = 0.0) -> Task:
    return Task(
        task_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        model_id=model_id,
        route="/v1/generate",
        method="POST",
        headers={},
        body=b"{}",
        status=TaskStatus.PENDING,
        created_at=created_at,
    )


async def test_create_and_get():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    fetched = await store.get(task.task_id)
    assert fetched is not None
    assert fetched.task_id == task.task_id


async def test_get_missing_returns_none():
    store = InMemoryTaskStore()
    assert await store.get("nonexistent") is None


async def test_claim_next_returns_none_when_empty():
    store = InMemoryTaskStore()
    assert await store.claim_next() is None


async def test_claim_next_returns_pending_task_and_sets_running():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    claimed = await store.claim_next()
    assert claimed is not None
    assert claimed.task_id == task.task_id
    updated = await store.get(task.task_id)
    assert updated.status == TaskStatus.RUNNING


async def test_claim_next_does_not_return_running_task():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    await store.claim_next()
    assert await store.claim_next() is None


async def test_claim_next_round_robins_across_tenants():
    store = InMemoryTaskStore()
    t1_task1 = make_task(tenant_id="t1", created_at=0.0)
    t1_task2 = make_task(tenant_id="t1", created_at=1.0)
    t2_task1 = make_task(tenant_id="t2", created_at=0.5)
    await store.create(t1_task1)
    await store.create(t1_task2)
    await store.create(t2_task1)

    claimed1 = await store.claim_next()
    assert claimed1.tenant_id == "t1"
    await store.update(claimed1.task_id, status=TaskStatus.DONE)

    claimed2 = await store.claim_next()
    assert claimed2.tenant_id == "t2"
    await store.update(claimed2.task_id, status=TaskStatus.DONE)

    claimed3 = await store.claim_next()
    assert claimed3.tenant_id == "t1"


async def test_update_task_fields():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    await store.update(task.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"ok")
    updated = await store.get(task.task_id)
    assert updated.status == TaskStatus.DONE
    assert updated.result_status == 200
    assert updated.result_body == b"ok"


async def test_queue_depth_counts_only_pending():
    store = InMemoryTaskStore()
    await store.create(make_task(tenant_id="t1"))
    await store.create(make_task(tenant_id="t1"))
    await store.create(make_task(tenant_id="t2"))
    assert await store.queue_depth("t1") == 2
    assert await store.queue_depth("t2") == 1
    assert await store.queue_depth("t3") == 0


async def test_queue_depth_excludes_running_and_done():
    store = InMemoryTaskStore()
    task = make_task(tenant_id="t1")
    await store.create(task)
    await store.claim_next()  # sets to RUNNING
    assert await store.queue_depth("t1") == 0


async def test_update_sets_completed_at_when_done():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    assert task.completed_at is None
    await store.update(task.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"")
    updated = await store.get(task.task_id)
    assert updated.completed_at is not None


async def test_update_sets_completed_at_when_failed():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    await store.update(task.task_id, status=TaskStatus.FAILED, error="boom")
    updated = await store.get(task.task_id)
    assert updated.completed_at is not None


async def test_update_does_not_set_completed_at_for_running():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    await store.update(task.task_id, status=TaskStatus.RUNNING)
    updated = await store.get(task.task_id)
    assert updated.completed_at is None


async def test_delete_expired_removes_tasks_past_ttl():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    await store.update(task.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"")
    # force completed_at to the past
    (await store.get(task.task_id)).completed_at = 0.0
    deleted = await store.delete_expired(ttl_seconds=1.0)
    assert deleted == 1
    assert await store.get(task.task_id) is None


async def test_delete_expired_keeps_recent_completed_tasks():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    await store.update(task.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"")
    # completed_at is just set — not expired yet with a long TTL
    deleted = await store.delete_expired(ttl_seconds=3600.0)
    assert deleted == 0
    assert await store.get(task.task_id) is not None


async def test_delete_expired_never_removes_pending_tasks():
    store = InMemoryTaskStore()
    task = make_task()
    await store.create(task)
    deleted = await store.delete_expired(ttl_seconds=0.0)
    assert deleted == 0
    assert await store.get(task.task_id) is not None
