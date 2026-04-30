import respx
from httpx import Response

from gpu_arbiter.config import HookConfig
from gpu_arbiter.lifecycle import LifecycleRunner


@respx.mock
def test_lifecycle_runner_calls_http_hook():
    route = respx.post("http://image-api:8003/admin/unload").mock(return_value=Response(200))
    runner = LifecycleRunner()

    runner.run_hook(HookConfig(type="http", url="http://image-api:8003/admin/unload"))

    assert route.called


@respx.mock
def test_lifecycle_runner_sends_hook_headers():
    route = respx.post("http://image-api:8003/admin/unload").mock(return_value=Response(200))
    runner = LifecycleRunner()

    runner.run_hook(
        HookConfig(
            type="http",
            url="http://image-api:8003/admin/unload",
            headers={"Authorization": "Bearer test-key"},
        )
    )

    assert route.calls.last.request.headers["Authorization"] == "Bearer test-key"


@respx.mock
def test_lifecycle_runner_sends_json_body():
    route = respx.post("http://ollama:11434/api/generate").mock(return_value=Response(200))
    runner = LifecycleRunner()

    runner.run_hook(
        HookConfig(
            type="http",
            url="http://ollama:11434/api/generate",
            body_json={"model": "gemma4:e2b", "keep_alive": 0},
        )
    )

    assert route.calls.last.request.content == b'{"model":"gemma4:e2b","keep_alive":0}'


@respx.mock
def test_lifecycle_runner_runs_multiple_hooks_in_order():
    calls = []

    def record(name):
        def _handler(request):
            calls.append(name)
            return Response(200)

        return _handler

    respx.post("http://ollama:11434/api/generate").mock(side_effect=record("ollama"))
    respx.post("http://image-api:8003/admin/unload").mock(side_effect=record("image"))
    runner = LifecycleRunner()

    runner.run_hooks(
        [
            HookConfig(type="http", url="http://ollama:11434/api/generate"),
            HookConfig(type="http", url="http://image-api:8003/admin/unload"),
        ]
    )

    assert calls == ["ollama", "image"]


@respx.mock
def test_lifecycle_runner_can_ignore_hook_errors():
    respx.post("http://image-api:8003/admin/unload").mock(return_value=Response(404))
    route = respx.post("http://ollama:11434/api/generate").mock(return_value=Response(200))
    runner = LifecycleRunner()

    runner.run_hooks(
        [
            HookConfig(type="http", url="http://image-api:8003/admin/unload"),
            HookConfig(type="http", url="http://ollama:11434/api/generate"),
        ],
        ignore_errors=True,
    )

    assert route.called


@respx.mock
def test_lifecycle_runner_waits_for_health_until_success():
    route = respx.get("http://image-api:8003/health").mock(
        side_effect=[Response(503), Response(200, json={"status": "ok"})]
    )
    runner = LifecycleRunner(poll_interval_seconds=0)

    runner.wait_for_health(HookConfig(type="http", url="http://image-api:8003/health", method="GET"))

    assert route.call_count == 2
