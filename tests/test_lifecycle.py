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
def test_lifecycle_runner_waits_for_health_until_success():
    route = respx.get("http://image-api:8003/health").mock(
        side_effect=[Response(503), Response(200, json={"status": "ok"})]
    )
    runner = LifecycleRunner(poll_interval_seconds=0)

    runner.wait_for_health(HookConfig(type="http", url="http://image-api:8003/health", method="GET"))

    assert route.call_count == 2
