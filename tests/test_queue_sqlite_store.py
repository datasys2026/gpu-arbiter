from __future__ import annotations

import uuid
import pytest

from gpu_arbiter.queue.models import Task, TaskStatus
from gpu_arbiter.queue.sqlite_store import SQLiteTaskStore


def make_task(tenant_id: str = "t1", model_id: str = "m1", created_at: float = 0.0) -> Task:
    return Task(
        task_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        model_id=model_id,
        route="/v1/generate",
        method="POST",
        headers={"content-type": "application/json"},
        body=b"{}",
        status=TaskStatus.PENDING,
        created_at=created_at,
    )


@pytest.fixture
async def store():
    s = SQLiteTaskStore(":memory:")
    await s.initialize()
    yield s
    await s.close()


# --- Protocol conformance ---

async def test_create_and_get(store):
    task = make_task()
    await store.create(task)
    fetched = await store.get(task.task_id)
    assert fetched is not None
    assert fetched.task_id == task.task_id
    assert fetched.tenant_id == task.tenant_id
    assert fetched.body == task.body


async def test_get_missing_returns_none(store):
    assert await store.get("nonexistent") is None


async def test_claim_next_returns_none_when_empty(store):
    assert await store.claim_next() is None


async def test_claim_next_sets_running(store):
    task = make_task()
    await store.create(task)
    claimed = await store.claim_next()
    assert claimed is not None
    assert claimed.task_id == task.task_id
    updated = await store.get(task.task_id)
    assert updated.status == TaskStatus.RUNNING


async def test_claim_next_skips_non_pending(store):
    task = make_task()
    await store.create(task)
    await store.claim_next()
    assert await store.claim_next() is None


async def test_claim_next_round_robins_across_tenants(store):
    t1a = make_task(tenant_id="t1", created_at=0.0)
    t1b = make_task(tenant_id="t1", created_at=1.0)
    t2a = make_task(tenant_id="t2", created_at=0.5)
    for t in [t1a, t1b, t2a]:
        await store.create(t)

    c1 = await store.claim_next()
    assert c1.tenant_id == "t1"
    await store.update(c1.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"")

    c2 = await store.claim_next()
    assert c2.tenant_id == "t2"
    await store.update(c2.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"")

    c3 = await store.claim_next()
    assert c3.tenant_id == "t1"


async def test_update_stores_result(store):
    task = make_task()
    await store.create(task)
    await store.update(
        task.task_id,
        status=TaskStatus.DONE,
        result_status=200,
        result_body=b'{"ok":true}',
        result_headers={"content-type": "application/json"},
    )
    updated = await store.get(task.task_id)
    assert updated.status == TaskStatus.DONE
    assert updated.result_status == 200
    assert updated.result_body == b'{"ok":true}'
    assert updated.result_headers == {"content-type": "application/json"}


async def test_update_sets_completed_at_for_terminal_states(store):
    task = make_task()
    await store.create(task)
    await store.update(task.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"")
    updated = await store.get(task.task_id)
    assert updated.completed_at is not None


async def test_queue_depth(store):
    await store.create(make_task(tenant_id="t1"))
    await store.create(make_task(tenant_id="t1"))
    await store.create(make_task(tenant_id="t2"))
    assert await store.queue_depth("t1") == 2
    assert await store.queue_depth("t2") == 1
    assert await store.queue_depth("t3") == 0


async def test_list_tasks(store):
    t1 = make_task(tenant_id="t1")
    t2 = make_task(tenant_id="t1")
    t3 = make_task(tenant_id="t2")
    for t in [t1, t2, t3]:
        await store.create(t)
    result = await store.list_tasks("t1")
    assert {r.task_id for r in result} == {t1.task_id, t2.task_id}


async def test_list_tasks_filters_by_status(store):
    task = make_task(tenant_id="t1")
    await store.create(task)
    await store.update(task.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"")
    pending = await store.list_tasks("t1", status=TaskStatus.PENDING)
    done = await store.list_tasks("t1", status=TaskStatus.DONE)
    assert pending == []
    assert len(done) == 1


async def test_delete_expired(store):
    task = make_task()
    await store.create(task)
    await store.update(task.task_id, status=TaskStatus.DONE, result_status=200, result_body=b"")
    # Force completed_at to the distant past by direct DB update
    async with store._db.execute(
        "UPDATE tasks SET completed_at = 0 WHERE task_id = ?", (task.task_id,)
    ):
        pass
    await store._db.commit()
    deleted = await store.delete_expired(ttl_seconds=1.0)
    assert deleted == 1
    assert await store.get(task.task_id) is None


# --- SQLite-specific: persistence across reconnections ---

async def test_persists_across_reconnections(tmp_path):
    db_path = str(tmp_path / "test.db")
    store1 = SQLiteTaskStore(db_path)
    await store1.initialize()
    task = make_task()
    await store1.create(task)
    await store1.close()

    store2 = SQLiteTaskStore(db_path)
    await store2.initialize()
    fetched = await store2.get(task.task_id)
    await store2.close()

    assert fetched is not None
    assert fetched.task_id == task.task_id
