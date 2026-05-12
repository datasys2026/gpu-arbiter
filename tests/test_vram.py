import pytest

from gpu_arbiter.vram import InsufficientVRAMError, StaticVRAMProbe, wait_for_vram_available


async def test_wait_for_vram_available_returns_immediately_when_already_free():
    probe = StaticVRAMProbe(free_mb=20000)
    sleeps: list[float] = []

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    await wait_for_vram_available(probe, required_mb=18000, timeout_seconds=60, sleep_fn=_sleep)

    assert sleeps == []


async def test_wait_for_vram_available_retries_until_vram_freed():
    free_values = iter([1000, 1000, 20000])

    class DynamicProbe:
        def get_free_mb(self) -> int:
            return next(free_values)

    sleeps: list[float] = []
    times = iter([0.0, 0.0, 0.0, 0.0, 1000.0])

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    await wait_for_vram_available(
        DynamicProbe(),
        required_mb=18000,
        timeout_seconds=500,
        sleep_fn=_sleep,
        now_fn=lambda: next(times),
    )

    assert len(sleeps) == 2


async def test_wait_for_vram_available_raises_on_timeout():
    probe = StaticVRAMProbe(free_mb=1000)
    times = iter([0.0, 0.0, 100.0])

    async def _sleep(s: float) -> None:
        pass

    with pytest.raises(InsufficientVRAMError) as exc_info:
        await wait_for_vram_available(
            probe,
            required_mb=18000,
            timeout_seconds=10,
            sleep_fn=_sleep,
            now_fn=lambda: next(times),
        )

    assert exc_info.value.required_mb == 18000
