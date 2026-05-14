import json
import logging

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from gpu_arbiter.app import ClientDisconnectedError, _proxy_with_disconnect_check, create_app
from gpu_arbiter.config import ArbiterConfig, GPUConfig, HealthConfig, HookConfig, ModelConfig
from gpu_arbiter.locking import InMemoryGPULock
from gpu_arbiter.vram import StaticVRAMProbe


def _parse_log_events(caplog) -> list[dict]:
    events = []
    for r in caplog.records:
        try:
            events.append(json.loads(r.getMessage()))
        except (json.JSONDecodeError, TypeError):
            pass
    return events


def _app(free_mb: int = 16000):
    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=12000,
            )
        },
    )
    return create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(free_mb))


def _app_with_hooks(free_mb: int = 16000):
    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=12000,
                unload=HookConfig(type="http", url="http://image-api:8003/admin/unload"),
            )
        },
    )
    return create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(free_mb))


def test_models_endpoint_lists_configured_models():
    client = TestClient(_app())

    response = client.get("/models")

    assert response.status_code == 200
    assert response.json()["data"] == [{"id": "aiark/z-image-turbo"}]


@respx.mock
def test_proxy_routes_by_model():
    respx.post("http://image-api:8003/v1/images/generations").mock(
        return_value=Response(200, json={"ok": True})
    )
    client = TestClient(_app())

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_proxy_returns_insufficient_vram():
    client = TestClient(_app(free_mb=1000))

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "insufficient_vram"


@respx.mock
def test_non_gpu_route_does_not_take_gpu_lock_or_check_vram():
    config = ArbiterConfig(
        gpu=GPUConfig(index=0, cooldown_seconds=99),
        models={
            "aiark/litellm-models": ModelConfig(
                route="/v1/models",
                upstream="http://litellm:4000",
                uses_gpu=False,
                required_vram_mb=0,
            )
        },
    )
    respx.get("http://litellm:4000/v1/models").mock(
        return_value=Response(200, json={"data": []})
    )
    client = TestClient(
        create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(0))
    )

    first = client.get("/v1/models")
    second = client.get("/v1/models")

    assert first.status_code == 200
    assert second.status_code == 200


@respx.mock
def test_proxy_unloads_before_upstream_request():
    calls = []

    def record(name):
        def _handler(request):
            calls.append(name)
            return Response(200, json={"ok": True})

        return _handler

    respx.post("http://image-api:8003/admin/unload").mock(side_effect=record("unload"))
    respx.post("http://image-api:8003/v1/images/generations").mock(side_effect=record("proxy"))
    client = TestClient(_app_with_hooks())

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 200
    assert calls == ["unload", "proxy"]


@respx.mock
def test_proxy_ignores_unload_hook_errors():
    respx.post("http://image-api:8003/admin/unload").mock(return_value=Response(404))
    respx.post("http://image-api:8003/v1/images/generations").mock(
        return_value=Response(200, json={"ok": True})
    )
    client = TestClient(_app_with_hooks())

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@respx.mock
def test_proxy_waits_for_upstream_health_before_forwarding():
    calls = []

    def record(name):
        def _handler(request):
            calls.append(name)
            return Response(200, json={"ok": True})
        return _handler

    respx.get("http://image-api:8003/health").mock(side_effect=record("health"))
    respx.post("http://image-api:8003/v1/images/generations").mock(side_effect=record("proxy"))

    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=12000,
                health=HealthConfig(url="http://image-api:8003/health", wait_timeout_seconds=30),
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(16000)))

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 200
    assert calls == ["health", "proxy"]


@respx.mock
def test_proxy_returns_503_when_upstream_health_times_out():
    respx.get("http://image-api:8003/health").mock(return_value=Response(503))

    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=0,
                health=HealthConfig(
                    url="http://image-api:8003/health",
                    wait_timeout_seconds=0.01,
                    poll_interval_seconds=0.001,
                ),
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(16000)))

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "upstream_not_ready"


def test_admin_unload_is_blocked_by_gpu_lock():
    lock = InMemoryGPULock()
    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                unload=HookConfig(type="http", url="http://image-api:8003/admin/unload"),
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=lock, vram_probe=StaticVRAMProbe(16000)))

    with lock.acquire("image-api"):
        response = client.post("/admin/unload")

    assert response.status_code == 409
    assert response.json()["error"]["type"] == "gpu_busy"


@respx.mock
def test_proxy_returns_504_when_upstream_exceeds_max_proxy_seconds():
    import anyio

    async def slow_upstream(request):
        await anyio.sleep(5)
        return Response(200, json={"ok": True})

    respx.post("http://image-api:8003/v1/images/generations").mock(side_effect=slow_upstream)
    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=0,
                max_proxy_seconds=0.1,
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(16000)))

    response = client.post("/v1/images/generations", json={"model": "aiark/z-image-turbo", "prompt": "test"})

    assert response.status_code == 504
    assert response.json()["error"]["type"] == "request_timeout"


@respx.mock
def test_gpu_lock_is_released_after_proxy_timeout():
    import anyio

    async def slow_upstream(request):
        await anyio.sleep(5)
        return Response(200, json={"ok": True})

    respx.post("http://image-api:8003/v1/images/generations").mock(side_effect=slow_upstream)
    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=0,
                max_proxy_seconds=0.1,
            )
        },
    )
    lock = InMemoryGPULock()
    client = TestClient(create_app(config, gpu_lock=lock, vram_probe=StaticVRAMProbe(16000)))

    response = client.post("/v1/images/generations", json={"model": "aiark/z-image-turbo", "prompt": "test"})

    assert response.status_code == 504
    assert lock.holder is None


@respx.mock
def test_cleanup_hooks_run_after_successful_request():
    calls = []

    def record(name, status=200):
        def _handler(request):
            calls.append(name)
            return Response(status, json={"ok": True})
        return _handler

    respx.post("http://image-api:8003/v1/images/generations").mock(side_effect=record("proxy"))
    respx.post("http://image-api:8003/admin/cleanup").mock(side_effect=record("cleanup"))

    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=0,
                cleanup=HookConfig(type="http", url="http://image-api:8003/admin/cleanup"),
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(16000)))

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 200
    assert calls == ["proxy", "cleanup"]


@respx.mock
def test_cleanup_hooks_run_after_failed_upstream_response():
    calls = []

    def record(name, status=200):
        def _handler(request):
            calls.append(name)
            return Response(status, json={"ok": True})
        return _handler

    respx.post("http://image-api:8003/v1/images/generations").mock(side_effect=record("proxy", 500))
    respx.post("http://image-api:8003/admin/cleanup").mock(side_effect=record("cleanup"))

    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=0,
                cleanup=HookConfig(type="http", url="http://image-api:8003/admin/cleanup"),
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(16000)))

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 500
    assert calls == ["proxy", "cleanup"]


@respx.mock
def test_cleanup_hooks_run_after_proxy_timeout():
    import anyio

    calls = []

    async def slow_upstream(request):
        await anyio.sleep(5)
        return Response(200, json={"ok": True})

    def record(name):
        def _handler(request):
            calls.append(name)
            return Response(200, json={"ok": True})
        return _handler

    respx.post("http://image-api:8003/v1/images/generations").mock(side_effect=slow_upstream)
    respx.post("http://image-api:8003/admin/cleanup").mock(side_effect=record("cleanup"))

    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=0,
                max_proxy_seconds=0.1,
                cleanup=HookConfig(type="http", url="http://image-api:8003/admin/cleanup"),
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(16000)))

    response = client.post("/v1/images/generations", json={"model": "aiark/z-image-turbo", "prompt": "test"})

    assert response.status_code == 504
    assert "cleanup" in calls


async def test_proxy_with_disconnect_check_raises_on_disconnect():
    import anyio

    async def immediately_disconnected():
        return True

    async def slow_proxy():
        await anyio.sleep(5)

    with pytest.raises(ClientDisconnectedError):
        await _proxy_with_disconnect_check(slow_proxy(), immediately_disconnected, poll_interval=0.01)


async def test_proxy_with_disconnect_check_returns_response_when_proxy_finishes_first():
    from starlette.responses import Response as StarletteResponse

    async def not_disconnected():
        return False

    async def fast_proxy():
        return StarletteResponse(content=b'{"ok": true}', status_code=200)

    result = await _proxy_with_disconnect_check(fast_proxy(), not_disconnected, poll_interval=0.01)
    assert result.status_code == 200


@respx.mock
def test_client_disconnect_returns_499_and_releases_lock(monkeypatch):
    import anyio

    poll_count = 0

    async def mock_is_disconnected(self):
        nonlocal poll_count
        poll_count += 1
        return poll_count >= 3

    import starlette.requests
    monkeypatch.setattr(starlette.requests.Request, "is_disconnected", mock_is_disconnected)

    async def slow_upstream(request):
        await anyio.sleep(5)
        return Response(200, json={"ok": True})

    respx.post("http://tts-api:8002/v1/audio/speech").mock(side_effect=slow_upstream)

    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/qwen3-tts-1.7b-base": ModelConfig(
                route="/v1/audio/speech",
                upstream="http://tts-api:8002",
                required_vram_mb=0,
            )
        },
    )
    lock = InMemoryGPULock()
    client = TestClient(
        create_app(config, gpu_lock=lock, vram_probe=StaticVRAMProbe(16000), disconnect_poll_interval=0.01)
    )

    response = client.post("/v1/audio/speech", json={"model": "aiark/qwen3-tts-1.7b-base", "text": "hello"})

    assert response.status_code == 499
    assert lock.holder is None


@respx.mock
def test_client_disconnect_runs_cleanup_hooks(monkeypatch):
    import anyio

    calls = []
    poll_count = 0

    async def mock_is_disconnected(self):
        nonlocal poll_count
        poll_count += 1
        return poll_count >= 3

    import starlette.requests
    monkeypatch.setattr(starlette.requests.Request, "is_disconnected", mock_is_disconnected)

    async def slow_upstream(request):
        await anyio.sleep(5)
        return Response(200, json={"ok": True})

    respx.post("http://tts-api:8002/v1/audio/speech").mock(side_effect=slow_upstream)
    respx.post("http://tts-api:8002/admin/unload").mock(return_value=Response(200, json={"ok": True}))

    def record_cleanup(request):
        calls.append("cleanup")
        return Response(200, json={"ok": True})

    respx.post("http://tts-api:8002/admin/unload").mock(side_effect=record_cleanup)

    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/qwen3-tts-1.7b-base": ModelConfig(
                route="/v1/audio/speech",
                upstream="http://tts-api:8002",
                required_vram_mb=0,
                cleanup=HookConfig(type="http", url="http://tts-api:8002/admin/unload"),
            )
        },
    )
    lock = InMemoryGPULock()
    client = TestClient(
        create_app(config, gpu_lock=lock, vram_probe=StaticVRAMProbe(16000), disconnect_poll_interval=0.01)
    )

    response = client.post("/v1/audio/speech", json={"model": "aiark/qwen3-tts-1.7b-base", "text": "hello"})

    assert response.status_code == 499
    assert "cleanup" in calls


def test_vram_headroom_mb_blocks_request_when_combined_exceeds_free():
    config = ArbiterConfig(
        gpu=GPUConfig(index=0, vram_headroom_mb=2000),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=10000,
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(11000)))

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "insufficient_vram"


@respx.mock
def test_vram_headroom_mb_allows_request_when_combined_fits():
    respx.post("http://image-api:8003/v1/images/generations").mock(
        return_value=Response(200, json={"ok": True})
    )
    config = ArbiterConfig(
        gpu=GPUConfig(index=0, vram_headroom_mb=2000),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=10000,
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(12000)))

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Phase logs and enriched error responses
# ---------------------------------------------------------------------------

@respx.mock
def test_gpu_request_emits_all_phase_logs(caplog):
    respx.post("http://image-api:8003/v1/images/generations").mock(
        return_value=Response(200, json={"ok": True})
    )
    with caplog.at_level(logging.INFO, logger="gpu_arbiter"):
        client = TestClient(_app())
        client.post(
            "/v1/images/generations",
            json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
        )

    events = _parse_log_events(caplog)
    names = {e["event"] for e in events}
    assert names >= {
        "phase_unload_start",
        "phase_unload_done",
        "phase_vram_wait_start",
        "phase_vram_ready",
        "phase_proxy_start",
        "phase_proxy_done",
    }


@respx.mock
def test_phase_proxy_done_includes_elapsed_ms_and_status_code(caplog):
    respx.post("http://image-api:8003/v1/images/generations").mock(
        return_value=Response(200, json={"ok": True})
    )
    with caplog.at_level(logging.INFO, logger="gpu_arbiter"):
        client = TestClient(_app())
        client.post(
            "/v1/images/generations",
            json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
        )

    events = _parse_log_events(caplog)
    done = next((e for e in events if e["event"] == "phase_proxy_done"), None)
    assert done is not None
    assert "elapsed_ms" in done
    assert done["status_code"] == 200


@respx.mock
def test_phase_vram_wait_start_includes_free_and_required_mb(caplog):
    respx.post("http://image-api:8003/v1/images/generations").mock(
        return_value=Response(200, json={"ok": True})
    )
    with caplog.at_level(logging.INFO, logger="gpu_arbiter"):
        client = TestClient(_app(free_mb=16000))
        client.post(
            "/v1/images/generations",
            json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
        )

    events = _parse_log_events(caplog)
    vram_start = next((e for e in events if e["event"] == "phase_vram_wait_start"), None)
    assert vram_start is not None
    assert vram_start["free_mb"] == 16000
    assert vram_start["required_mb"] == 12000


def test_gpu_busy_response_includes_held_seconds():
    lock = InMemoryGPULock()
    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=0,
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=lock, vram_probe=StaticVRAMProbe(16000)))

    with lock.acquire("other-model"):
        response = client.post(
            "/v1/images/generations",
            json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
        )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["type"] == "gpu_busy"
    assert "held_seconds" in error
    assert error["held_seconds"] is not None


@respx.mock
def test_upstream_not_ready_response_includes_waited_seconds(caplog):
    respx.get("http://image-api:8003/health").mock(return_value=Response(503))

    config = ArbiterConfig(
        gpu=GPUConfig(index=0),
        models={
            "aiark/z-image-turbo": ModelConfig(
                route="/v1/images/generations",
                upstream="http://image-api:8003",
                required_vram_mb=0,
                health=HealthConfig(
                    url="http://image-api:8003/health",
                    wait_timeout_seconds=0.01,
                    poll_interval_seconds=0.001,
                ),
            )
        },
    )
    client = TestClient(create_app(config, gpu_lock=InMemoryGPULock(), vram_probe=StaticVRAMProbe(16000)))

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["type"] == "upstream_not_ready"
    assert "waited_seconds" in error
