import asyncio
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import MagicMock

import pytest
from deepagents.backends.protocol import (
    DeleteResult,
    ExecuteResponse,
    SandboxBackendProtocol,
)
from langchain_core.runnables.config import var_child_runnable_config

from openswe.sandboxes.state import (
    SANDBOX_BACKENDS,
    SandboxBackendProxy,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_id_from_metadata,
)


class _FakeSandboxBackend:
    id = "sandbox-1"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output=f"{self.id}: {command}: {timeout}", exit_code=0)

    async def adelete(self, file_path: str) -> DeleteResult:
        return DeleteResult(path=file_path)


@pytest.mark.asyncio
async def test_sandbox_proxy_reconnects_from_metadata_once(monkeypatch: pytest.MonkeyPatch) -> None:
    thread_id = "thread-1"
    SANDBOX_BACKENDS.pop(thread_id, None)
    created: list[str] = []

    async def get_sandbox_id_from_metadata(requested_thread_id: str) -> str:
        assert requested_thread_id == thread_id
        return "sandbox-1"

    async def connect_sandbox(sandbox_id: str, *, thread_id: str | None = None):
        created.append(sandbox_id)
        await asyncio.sleep(0)
        return _FakeSandboxBackend()

    monkeypatch.setattr(
        "openswe.sandboxes.state.get_sandbox_id_from_metadata",
        get_sandbox_id_from_metadata,
    )
    monkeypatch.setattr("openswe.sandboxes.connect.connect_sandbox", connect_sandbox)

    proxy = get_or_create_sandbox_backend_proxy(thread_id)
    assert SANDBOX_BACKENDS[thread_id] is proxy

    results = await asyncio.gather(*(proxy.aexecute(f"cmd-{idx}") for idx in range(5)))

    assert created == ["sandbox-1"]
    assert [result.output for result in results] == [
        "sandbox-1: cmd-0: 300",
        "sandbox-1: cmd-1: 300",
        "sandbox-1: cmd-2: 300",
        "sandbox-1: cmd-3: 300",
        "sandbox-1: cmd-4: 300",
    ]
    assert proxy.current.id == "sandbox-1"
    SANDBOX_BACKENDS.pop(thread_id, None)


@pytest.mark.asyncio
async def test_sandbox_metadata_ignores_run_config_from_before_rebind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def indexed(thread_id: str) -> MagicMock:
        return MagicMock(thread_id=thread_id, metadata={"sandbox_id": "sandbox-new"})

    monkeypatch.setattr("openswe.threads.store.THREADS.get", indexed)
    token = var_child_runnable_config.set({"metadata": {"sandbox_id": "sandbox-old"}})
    try:
        assert await get_sandbox_id_from_metadata("thread-1") == "sandbox-new"
    finally:
        var_child_runnable_config.reset(token)


@pytest.mark.asyncio
async def test_sandbox_proxy_startup_survives_waiter_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def reconnect():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _FakeSandboxBackend()

    proxy = SandboxBackendProxy(
        thread_id="thread-1",
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
    )
    proxy.start()
    waiter = asyncio.create_task(proxy.ready())
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    release.set()
    backend = await proxy.ready()

    assert backend.id == "sandbox-1"
    assert calls == 1


@pytest.mark.asyncio
async def test_sandbox_proxy_retries_failed_startup() -> None:
    calls = 0

    async def reconnect():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("startup failed")
        return _FakeSandboxBackend()

    proxy = SandboxBackendProxy(
        thread_id="thread-1",
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
    )
    proxy.start()

    with pytest.raises(RuntimeError, match="startup failed"):
        await proxy.ready()
    backend = await proxy.ready()

    assert backend.id == "sandbox-1"
    assert calls == 2


@pytest.mark.asyncio
async def test_sandbox_proxy_delegates_delete_after_lazy_startup() -> None:
    async def reconnect():
        return _FakeSandboxBackend()

    proxy = SandboxBackendProxy(
        thread_id="thread-1",
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
    )

    result = await proxy.adelete("/workspace/file.txt")

    assert result.path == "/workspace/file.txt"


@pytest.mark.parametrize(
    ("bound", "kept"), [("sandbox-1", True), ("sandbox-mda", False), (None, False)]
)
def test_a_cached_sandbox_the_thread_left_is_forgotten(bound: str | None, kept: bool) -> None:
    """Another replica rebinds the thread (a move off a Mac, a hand-over to MDA); this one follows."""
    proxy = SandboxBackendProxy(
        cast("SandboxBackendProtocol", _FakeSandboxBackend()), thread_id="thread-follow"
    )

    proxy.follow(bound)

    assert proxy.has_backend is kept


@pytest.mark.asyncio
async def test_evicts_idle_sandboxes_while_preserving_live_work_and_shared_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.sandboxes import state

    state.SANDBOX_BACKENDS.clear()
    state.SANDBOX_CONNECTIONS.clear()
    clock = 1000.0
    monkeypatch.setattr(state.time, "monotonic", lambda: clock)
    monkeypatch.setattr(state._SANDBOX_CACHE, "limit", 2)
    monkeypatch.setattr(state._SANDBOX_CACHE, "idle_ttl_seconds", 60.0)

    gate = asyncio.Event()

    class _Box(_FakeSandboxBackend):
        def __init__(self, box_id: str, *, wait: bool = False) -> None:
            self.id = box_id
            self._wait = wait

        async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
            if self._wait:
                await gate.wait()
            return ExecuteResponse(output=f"{self.id}: {command}", exit_code=0)

    busy_proxy = state.set_sandbox_backend(
        "t-busy", cast("SandboxBackendProtocol", _Box("sb-busy", wait=True))
    )
    running = asyncio.create_task(busy_proxy.aexecute("long"))
    await asyncio.sleep(0)

    clock += 10.0
    shared = cast("SandboxBackendProtocol", _Box("sb-shared"))
    state.set_sandbox_backend("t-shared-old", shared)

    clock += 70.0
    # Past TTL: t-busy has an in-flight call so it stays; t-shared-old is idle so it evicts,
    # unless another active thread still holds sb-shared.
    state.set_sandbox_backend("t-shared-live", shared)
    assert "t-busy" in state.SANDBOX_BACKENDS
    assert "t-shared-old" not in state.SANDBOX_BACKENDS
    assert "sb-shared" in state.SANDBOX_CONNECTIONS
    assert "sb-busy" in state.SANDBOX_CONNECTIONS

    # Exceeding the LRU limit (2) skips t-busy and evicts the oldest idle entry (t-shared-live),
    # releasing sb-shared now that no remaining thread holds it.
    clock += 1.0
    state.set_sandbox_backend("t-new-1", cast("SandboxBackendProtocol", _Box("sb-1")))
    clock += 1.0
    state.set_sandbox_backend("t-new-2", cast("SandboxBackendProtocol", _Box("sb-2")))

    assert set(state.SANDBOX_BACKENDS) == {"t-busy", "t-new-1", "t-new-2"}
    assert "sb-shared" not in state.SANDBOX_CONNECTIONS
    assert "sb-busy" in state.SANDBOX_CONNECTIONS

    gate.set()
    await running
    state.SANDBOX_BACKENDS.clear()
    state.SANDBOX_CONNECTIONS.clear()


@pytest.mark.asyncio
async def test_over_cap_eviction_drops_the_sandbox_used_least_recently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.sandboxes import state

    state.SANDBOX_BACKENDS.clear()
    state.SANDBOX_CONNECTIONS.clear()
    clock = 1000.0
    monkeypatch.setattr(state.time, "monotonic", lambda: clock)
    monkeypatch.setattr(state._SANDBOX_CACHE, "limit", 2)

    class _Box(_FakeSandboxBackend):
        def __init__(self, box_id: str) -> None:
            self.id = box_id

    first = state.set_sandbox_backend("t-first", cast("SandboxBackendProtocol", _Box("sb-first")))
    clock += 1.0
    state.set_sandbox_backend("t-second", cast("SandboxBackendProtocol", _Box("sb-second")))
    clock += 1.0
    await first.aexecute("ls")
    clock += 1.0
    state.set_sandbox_backend("t-third", cast("SandboxBackendProtocol", _Box("sb-third")))

    assert set(state.SANDBOX_BACKENDS) == {"t-first", "t-third"}
    assert set(state.SANDBOX_CONNECTIONS) == {"sb-first", "sb-third"}
    state.SANDBOX_BACKENDS.clear()
    state.SANDBOX_CONNECTIONS.clear()
