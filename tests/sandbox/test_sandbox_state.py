import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

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
    class _Threads:
        async def get(self, thread_id: str) -> dict[str, object]:
            return {"thread_id": thread_id, "metadata": {"sandbox_id": "sandbox-new"}}

    class _Client:
        threads = _Threads()

    monkeypatch.setattr("openswe.sandboxes.state.get_client", lambda: _Client())
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
