import respx
from fastapi.testclient import TestClient
from httpx import Response

from gpu_arbiter.app import create_app
from gpu_arbiter.config import ArbiterConfig, GPUConfig, HookConfig, ModelConfig
from gpu_arbiter.locking import InMemoryGPULock
from gpu_arbiter.vram import StaticVRAMProbe


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
                health=HookConfig(type="http", url="http://image-api:8003/health", method="GET"),
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
def test_proxy_unloads_and_waits_for_health_before_upstream_request():
    calls = []

    def record(name):
        def _handler(request):
            calls.append(name)
            return Response(200, json={"ok": True})

        return _handler

    respx.post("http://image-api:8003/admin/unload").mock(side_effect=record("unload"))
    respx.get("http://image-api:8003/health").mock(side_effect=record("health"))
    respx.post("http://image-api:8003/v1/images/generations").mock(side_effect=record("proxy"))
    client = TestClient(_app_with_hooks())

    response = client.post(
        "/v1/images/generations",
        json={"model": "aiark/z-image-turbo", "prompt": "a robot"},
    )

    assert response.status_code == 200
    assert calls == ["unload", "health", "proxy"]


@respx.mock
def test_proxy_ignores_missing_unload_hooks():
    respx.post("http://image-api:8003/admin/unload").mock(return_value=Response(404))
    respx.get("http://image-api:8003/health").mock(return_value=Response(200))
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
