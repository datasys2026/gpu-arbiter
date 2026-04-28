import respx
from fastapi.testclient import TestClient
from httpx import Response

from gpu_arbiter.app import create_app
from gpu_arbiter.config import ArbiterConfig, GPUConfig, ModelConfig
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
