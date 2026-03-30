from __future__ import annotations

from agent import server


class _BackendWithId:
    id = "sandbox-123"


class _BackendWithoutId:
    pass


def test_resolved_sandbox_id_prefers_backend_id() -> None:
    assert server._resolved_sandbox_id(_BackendWithId(), "thread-1") == "sandbox-123"


def test_resolved_sandbox_id_falls_back_for_local_backend() -> None:
    assert server._resolved_sandbox_id(_BackendWithoutId(), "thread-1") == "local-thread-1"