from gpu_arbiter.vram import InsufficientVRAMError, StaticVRAMProbe


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
