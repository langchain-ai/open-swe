"""The per-thread backend cache has to notice when a thread changes machines."""

from unittest.mock import MagicMock

from agent.utils.sandbox_state import (
    SANDBOX_BACKENDS,
    clear_sandbox_backend,
    get_or_create_sandbox_backend_proxy,
)


def _clear(thread_id: str) -> None:
    clear_sandbox_backend(thread_id)
    SANDBOX_BACKENDS.pop(thread_id, None)


def test_the_same_location_reuses_the_cached_handle() -> None:
    _clear("thread-1")
    first = get_or_create_sandbox_backend_proxy("thread-1", run_location="cloud")
    second = get_or_create_sandbox_backend_proxy("thread-1", run_location="cloud")

    assert first is second


def test_a_handle_cached_without_a_location_adopts_the_first_one_given() -> None:
    """Callers that only want the handle pass no location.

    Treating that as a mismatch would throw the sandbox away on the next run,
    which is the opposite of what the cache is for.
    """
    _clear("thread-2")
    first = get_or_create_sandbox_backend_proxy("thread-2")
    second = get_or_create_sandbox_backend_proxy("thread-2", run_location="cloud")

    assert first is second
    assert second.run_location == "cloud"


def test_moving_between_a_sandbox_and_a_workstation_drops_the_handle() -> None:
    _clear("thread-3")
    cloud = get_or_create_sandbox_backend_proxy("thread-3", run_location="cloud")
    cloud.replace_backend(MagicMock())
    local = get_or_create_sandbox_backend_proxy("thread-3", run_location="local")

    assert local is not cloud, "a local run must not inherit the cloud sandbox"
    assert local.has_backend is False
    _clear("thread-3")
