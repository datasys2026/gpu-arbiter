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
