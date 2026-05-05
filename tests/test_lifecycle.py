import pytest
import respx
from httpx import Response

from gpu_arbiter.config import HookConfig
from gpu_arbiter.lifecycle import LifecycleRunner


@pytest.mark.anyio
@respx.mock
async def test_lifecycle_runner_calls_http_hook():
    route = respx.post("http://image-api:8003/admin/unload").mock(return_value=Response(200))
    runner = LifecycleRunner()

    await runner.run_hook(HookConfig(type="http", url="http://image-api:8003/admin/unload"))

    assert route.called


@pytest.mark.anyio
@respx.mock
async def test_lifecycle_runner_sends_hook_headers():
    route = respx.post("http://image-api:8003/admin/unload").mock(return_value=Response(200))
    runner = LifecycleRunner()

    await runner.run_hook(
        HookConfig(
            type="http",
            url="http://image-api:8003/admin/unload",
            headers={"Authorization": "Bearer test-key"},
        )
    )

    assert route.calls.last.request.headers["Authorization"] == "Bearer test-key"


@pytest.mark.anyio
@respx.mock
async def test_lifecycle_runner_sends_json_body():
    route = respx.post("http://ollama:11434/api/generate").mock(return_value=Response(200))
    runner = LifecycleRunner()

    await runner.run_hook(
        HookConfig(
            type="http",
            url="http://ollama:11434/api/generate",
            body_json={"model": "gemma4:e2b", "keep_alive": 0},
        )
    )

    assert route.calls.last.request.content == b'{"model":"gemma4:e2b","keep_alive":0}'


@pytest.mark.anyio
@respx.mock
async def test_lifecycle_runner_runs_multiple_hooks_in_order():
    calls = []

    def record(name):
        def _handler(request):
            calls.append(name)
            return Response(200)

        return _handler

    respx.post("http://ollama:11434/api/generate").mock(side_effect=record("ollama"))
    respx.post("http://image-api:8003/admin/unload").mock(side_effect=record("image"))
    runner = LifecycleRunner()

    await runner.run_hooks(
        [
            HookConfig(type="http", url="http://ollama:11434/api/generate"),
            HookConfig(type="http", url="http://image-api:8003/admin/unload"),
        ]
    )

    assert calls == ["ollama", "image"]


@pytest.mark.anyio
@respx.mock
async def test_lifecycle_runner_can_ignore_hook_errors():
    respx.post("http://image-api:8003/admin/unload").mock(return_value=Response(404))
    route = respx.post("http://ollama:11434/api/generate").mock(return_value=Response(200))
    runner = LifecycleRunner()

    await runner.run_hooks(
        [
            HookConfig(type="http", url="http://image-api:8003/admin/unload"),
            HookConfig(type="http", url="http://ollama:11434/api/generate"),
        ],
        ignore_errors=True,
    )

    assert route.called

