from __future__ import annotations

from fastapi.testclient import TestClient

from gpu_arbiter.config import ArbiterConfig
from gpu_arbiter.app import create_app
from gpu_arbiter.queue.models import TaskStatus
from gpu_arbiter.queue.store import InMemoryTaskStore


def _make_config() -> ArbiterConfig:
    return ArbiterConfig.model_validate({
        "gpu": {"index": 0},
        "models": {
            "local/chat": {
                "route": "/v1/chat/completions",
                "upstream": "http://ollama:11434",
                "uses_gpu": True,
                "required_vram_mb": 0,
            }
        },
    })


def _make_client(store: InMemoryTaskStore | None = None) -> tuple[TestClient, InMemoryTaskStore]:
    from gpu_arbiter.vram import StaticVRAMProbe
    task_store = store or InMemoryTaskStore()
    config = _make_config()
    app = create_app(config, vram_probe=StaticVRAMProbe(free_mb=99999), task_store=task_store)
    return TestClient(app, raise_server_exceptions=True), task_store


# --- POST /queue ---

def test_submit_task_returns_task_id():
    client, store = _make_client()
    resp = client.post(
        "/queue",
        json={"model": "local/chat", "messages": []},
        headers={"X-Tenant-ID": "tenant-a", "Content-Type": "application/json"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "task_id" in body
    assert body["status"] == "pending"


def test_submit_task_requires_tenant_id():
    client, _ = _make_client()
    resp = client.post(
        "/queue",
        json={"model": "local/chat"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_submit_task_unknown_model_returns_404():
    client, _ = _make_client()
    resp = client.post(
        "/queue",
        json={"model": "nonexistent/model"},
        headers={"X-Tenant-ID": "tenant-a", "Content-Type": "application/json"},
    )
    assert resp.status_code == 404


def test_submit_task_respects_max_queue_depth():
    client, store = _make_client()
    for _ in range(10):
        resp = client.post(
            "/queue",
            json={"model": "local/chat"},
            headers={"X-Tenant-ID": "tenant-a", "Content-Type": "application/json"},
        )
        assert resp.status_code == 202
    resp = client.post(
        "/queue",
        json={"model": "local/chat"},
        headers={"X-Tenant-ID": "tenant-a", "Content-Type": "application/json"},
    )
    assert resp.status_code == 429


# --- GET /tasks/{task_id} ---

def test_get_task_pending():
    client, _ = _make_client()
    submit = client.post(
        "/queue",
        json={"model": "local/chat"},
        headers={"X-Tenant-ID": "tenant-a", "Content-Type": "application/json"},
    )
    task_id = submit.json()["task_id"]

    resp = client.get(f"/tasks/{task_id}", headers={"X-Tenant-ID": "tenant-a"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert resp.json()["result"] is None


def test_get_task_tenant_scoped():
    client, _ = _make_client()
    submit = client.post(
        "/queue",
        json={"model": "local/chat"},
        headers={"X-Tenant-ID": "tenant-a", "Content-Type": "application/json"},
    )
    task_id = submit.json()["task_id"]

    resp = client.get(f"/tasks/{task_id}", headers={"X-Tenant-ID": "tenant-b"})
    assert resp.status_code == 404


def test_get_task_not_found():
    client, _ = _make_client()
    resp = client.get("/tasks/doesnotexist", headers={"X-Tenant-ID": "tenant-a"})
    assert resp.status_code == 404


def test_get_task_done_includes_result():
    client, store = _make_client()
    submit = client.post(
        "/queue",
        json={"model": "local/chat"},
        headers={"X-Tenant-ID": "tenant-a", "Content-Type": "application/json"},
    )
    task_id = submit.json()["task_id"]
    import asyncio
    asyncio.run(
        store.update(
            task_id,
            status=TaskStatus.DONE,
            result_status=200,
            result_body=b'{"choices":[]}',
            result_headers={"content-type": "application/json"},
        )
    )

    resp = client.get(f"/tasks/{task_id}", headers={"X-Tenant-ID": "tenant-a"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["result"]["status_code"] == 200


# --- GET /queue/status ---

def test_queue_status_returns_counts():
    client, _ = _make_client()
    client.post(
        "/queue",
        json={"model": "local/chat"},
        headers={"X-Tenant-ID": "tenant-a", "Content-Type": "application/json"},
    )
    client.post(
        "/queue",
        json={"model": "local/chat"},
        headers={"X-Tenant-ID": "tenant-b", "Content-Type": "application/json"},
    )

    resp = client.get("/queue/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending"] == 2
    assert body["running"] == 0
    assert set(body["tenants"]) == {"tenant-a", "tenant-b"}
