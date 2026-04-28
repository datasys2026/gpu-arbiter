from gpu_arbiter.locking import GPUBusyError, InMemoryGPULock


def test_nonblocking_lock_reports_busy():
    lock = InMemoryGPULock()

    with lock.acquire("image-api"):
        try:
            with lock.acquire("tts-api"):
                raise AssertionError("second acquire should not succeed")
        except GPUBusyError as exc:
            assert exc.holder == "image-api"


def test_lock_releases_after_context():
    lock = InMemoryGPULock()

    with lock.acquire("image-api"):
        pass

    with lock.acquire("tts-api"):
        assert lock.holder == "tts-api"
