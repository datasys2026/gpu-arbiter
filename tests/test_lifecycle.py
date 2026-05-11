import pytest
import respx
from httpx import Response

from gpu_arbiter.config import HealthConfig, HookConfig
from gpu_arbiter.lifecycle import LifecycleRunner, UpstreamNotReadyError


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


@pytest.mark.anyio
@respx.mock
async def test_wait_until_ready_returns_immediately_when_healthy():
    respx.get("http://image-api:8003/health").mock(return_value=Response(200))
    runner = LifecycleRunner()
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    await runner.wait_until_ready(
        HealthConfig(url="http://image-api:8003/health", wait_timeout_seconds=30),
        sleep_fn=_sleep,
    )

    assert sleeps == []


@pytest.mark.anyio
@respx.mock
async def test_wait_until_ready_polls_until_service_recovers():
    call_count = 0

    def _handler(request):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return Response(503)
        return Response(200)

    respx.get("http://image-api:8003/health").mock(side_effect=_handler)
    runner = LifecycleRunner()
    sleeps: list[float] = []
    times = iter([0.0, 1.0, 2.0, 3.0, 4.0])

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    await runner.wait_until_ready(
        HealthConfig(url="http://image-api:8003/health", wait_timeout_seconds=60),
        sleep_fn=_sleep,
        now_fn=lambda: next(times),
    )

    assert call_count == 3
    assert len(sleeps) == 2


@pytest.mark.anyio
@respx.mock
async def test_wait_until_ready_raises_after_timeout():
    respx.get("http://image-api:8003/health").mock(return_value=Response(503))
    runner = LifecycleRunner()
    times = iter([0.0, 5.0, 200.0])

    async def _sleep(s: float) -> None:
        pass

    with pytest.raises(UpstreamNotReadyError):
        await runner.wait_until_ready(
            HealthConfig(url="http://image-api:8003/health", wait_timeout_seconds=10),
            sleep_fn=_sleep,
            now_fn=lambda: next(times),
        )


@pytest.mark.anyio
async def test_wait_until_ready_skips_when_health_is_none():
    runner = LifecycleRunner()
    await runner.wait_until_ready(None)
