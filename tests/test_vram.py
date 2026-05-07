from gpu_arbiter.vram import InsufficientVRAMError, StaticVRAMProbe, wait_for_vram_available


def test_vram_probe_allows_enough_memory():
    probe = StaticVRAMProbe(free_mb=16000)

    probe.ensure_available(required_mb=8000)


def test_vram_probe_raises_when_insufficient():
    probe = StaticVRAMProbe(free_mb=1000)

    try:
        probe.ensure_available(required_mb=8000)
        raise AssertionError("expected insufficient VRAM")
    except InsufficientVRAMError as exc:
        assert exc.free_mb == 1000
        assert exc.required_mb == 8000


def test_wait_for_vram_available_returns_immediately_when_already_free():
    probe = StaticVRAMProbe(free_mb=20000)
    sleeps: list[float] = []

    wait_for_vram_available(probe, required_mb=18000, timeout_seconds=60, sleep_fn=lambda s: sleeps.append(s))

    assert sleeps == []


def test_wait_for_vram_available_retries_until_vram_freed():
    free_values = iter([1000, 1000, 20000])

    class DynamicProbe:
        def get_free_mb(self) -> int:
            return next(free_values)

    sleeps: list[float] = []
    times = iter([0.0, 0.0, 0.0, 0.0, 1000.0])

    wait_for_vram_available(
        DynamicProbe(),
        required_mb=18000,
        timeout_seconds=500,
        sleep_fn=lambda s: sleeps.append(s),
        now_fn=lambda: next(times),
    )

    assert len(sleeps) == 2


def test_wait_for_vram_available_raises_on_timeout():
    probe = StaticVRAMProbe(free_mb=1000)
    times = iter([0.0, 0.0, 100.0])

    try:
        wait_for_vram_available(
            probe,
            required_mb=18000,
            timeout_seconds=10,
            sleep_fn=lambda s: None,
            now_fn=lambda: next(times),
        )
        raise AssertionError("expected InsufficientVRAMError")
    except InsufficientVRAMError as exc:
        assert exc.required_mb == 18000
