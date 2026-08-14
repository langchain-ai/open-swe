from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import (
    DeleteResult,
    ExecuteOffloadResult,
    ExecuteResponse,
    SandboxBackendProtocol,
)
from deepagents.backends.sandbox import BaseSandbox

from agent.utils.sandbox_state import (
    SANDBOX_BACKENDS,
    SandboxBackendProxy,
    clear_sandbox_backend,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_backend,
    get_sandbox_id_from_metadata,
)


class _FakeSandboxBackend:
    id = "sandbox-1"

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output=f"{self.id}: {command}: {timeout}", exit_code=0)

    async def adelete(self, file_path: str) -> DeleteResult:
        return DeleteResult(path=file_path)


class _OffloadCapableBackend(BaseSandbox):
    """Minimal BaseSandbox whose offload records how it was called."""

    def __init__(self) -> None:
        self.offload_calls: list[dict[str, object]] = []

    @property
    def id(self) -> str:
        return "offload-sandbox"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output=command, exit_code=0)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output=command, exit_code=0)

    def upload_files(self, files):  # noqa: ANN001, ANN201 - unused stub
        return []

    def download_files(self, paths):  # noqa: ANN001, ANN201 - unused stub
        return []

    async def aexecute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int | None = None,
    ) -> ExecuteOffloadResult:
        self.offload_calls.append(
            {
                "command": command,
                "capture_path": capture_path,
                "max_inline_bytes": max_inline_bytes,
                "timeout": timeout,
            }
        )
        return ExecuteOffloadResult(
            offloaded=True, response=ExecuteResponse(output="preview", exit_code=0)
        )


def test_sandbox_proxy_is_capture_offload_capable() -> None:
    # FilesystemMiddleware._resolve_capture gates the execute capture-at-source
    # path on isinstance(backend, BaseSandbox); the proxy must satisfy it or the
    # tool falls back to plain execute and pulls full stdout into the worker.
    assert issubclass(SandboxBackendProxy, BaseSandbox)
    assert isinstance(SandboxBackendProxy(thread_id="t"), BaseSandbox)


@pytest.mark.asyncio
async def test_sandbox_proxy_delegates_offload_to_live_backend() -> None:
    backend = _OffloadCapableBackend()
    proxy = SandboxBackendProxy(backend, thread_id="t")

    result = await proxy.aexecute_with_offload(
        "run tests", "/capture/path", max_inline_bytes=80_000, timeout=30
    )

    assert result.offloaded is True
    assert result.response.output == "preview"
    assert backend.offload_calls == [
        {
            "command": "run tests",
            "capture_path": "/capture/path",
            "max_inline_bytes": 80_000,
            "timeout": 30,
        }
    ]


@pytest.mark.asyncio
async def test_sandbox_proxy_offload_falls_back_when_backend_lacks_it() -> None:
    # A backend implementing only the protocol (no capture-offload) must not
    # error: the proxy runs it plainly and reports offloaded=False.
    proxy = SandboxBackendProxy(cast(SandboxBackendProtocol, _FakeSandboxBackend()), thread_id="t")

    result = await proxy.aexecute_with_offload("cmd", "/capture/path", max_inline_bytes=80_000)

    assert result.offloaded is False
    assert result.response.output == "sandbox-1: cmd: None"


@pytest.mark.asyncio
async def test_sandbox_proxy_reconnects_from_metadata_once(monkeypatch: pytest.MonkeyPatch) -> None:
    thread_id = "thread-1"
    clear_sandbox_backend(thread_id)
    created: list[str] = []

    async def get_sandbox_id_from_metadata(requested_thread_id: str) -> str:
        assert requested_thread_id == thread_id
        return "sandbox-1"

    async def create_sandbox(sandbox_id: str):
        created.append(sandbox_id)
        await asyncio.sleep(0)
        return _FakeSandboxBackend()

    monkeypatch.setattr(
        "agent.utils.sandbox_state.get_sandbox_id_from_metadata",
        get_sandbox_id_from_metadata,
    )
    monkeypatch.setattr("agent.utils.sandbox_state.create_sandbox", create_sandbox)

    proxy = get_or_create_sandbox_backend_proxy(thread_id)
    assert SANDBOX_BACKENDS[thread_id] is proxy

    results = await asyncio.gather(*(proxy.aexecute(f"cmd-{idx}") for idx in range(5)))

    assert created == ["sandbox-1"]
    assert [result.output for result in results] == [
        "sandbox-1: cmd-0: None",
        "sandbox-1: cmd-1: None",
        "sandbox-1: cmd-2: None",
        "sandbox-1: cmd-3: None",
        "sandbox-1: cmd-4: None",
    ]
    assert proxy.current.id == "sandbox-1"
    clear_sandbox_backend(thread_id)


@pytest.mark.asyncio
async def test_sandbox_proxy_uses_registered_reconnect_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "thread-1"
    clear_sandbox_backend(thread_id)
    reconnected: list[str] = []

    async def reconnect():
        reconnected.append(thread_id)
        await asyncio.sleep(0)
        return _FakeSandboxBackend()

    async def create_sandbox(sandbox_id: str):
        raise AssertionError(f"unexpected direct reconnect to {sandbox_id}")

    monkeypatch.setattr("agent.utils.sandbox_state.create_sandbox", create_sandbox)

    proxy = get_or_create_sandbox_backend_proxy(
        thread_id,
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
    )
    results = await asyncio.gather(*(proxy.aexecute(f"cmd-{idx}") for idx in range(5)))

    assert reconnected == [thread_id]
    assert [result.output for result in results] == [
        "sandbox-1: cmd-0: None",
        "sandbox-1: cmd-1: None",
        "sandbox-1: cmd-2: None",
        "sandbox-1: cmd-3: None",
        "sandbox-1: cmd-4: None",
    ]
    clear_sandbox_backend(thread_id)


@pytest.mark.asyncio
async def test_sandbox_proxy_refreshes_initialized_backend_when_started() -> None:
    refreshed = _FakeSandboxBackend()
    calls = 0

    async def reconnect():
        nonlocal calls
        calls += 1
        return refreshed

    proxy = SandboxBackendProxy(
        cast(SandboxBackendProtocol, _FakeSandboxBackend()),
        thread_id="thread-1",
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
    )
    proxy.start()

    assert await proxy.ready() is refreshed
    assert calls == 1


@pytest.mark.asyncio
async def test_sandbox_proxy_starts_reconnect_before_first_operation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def reconnect():
        started.set()
        await release.wait()
        return _FakeSandboxBackend()

    proxy = SandboxBackendProxy(
        thread_id="thread-1",
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
    )
    proxy.start()

    await started.wait()
    assert not proxy.has_backend
    release.set()
    backend = await proxy.ready()

    assert backend.id == "sandbox-1"
    assert proxy.has_backend


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


@pytest.mark.asyncio
async def test_get_sandbox_backend_awaits_registered_startup() -> None:
    thread_id = "thread-1"
    clear_sandbox_backend(thread_id)
    release = asyncio.Event()

    async def reconnect():
        await release.wait()
        return _FakeSandboxBackend()

    proxy = get_or_create_sandbox_backend_proxy(
        thread_id,
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
    )
    proxy.start()
    waiter = asyncio.create_task(get_sandbox_backend(thread_id))
    await asyncio.sleep(0)
    assert not waiter.done()

    release.set()
    assert await waiter is proxy
    clear_sandbox_backend(thread_id)


@pytest.mark.asyncio
async def test_sandbox_id_metadata_falls_back_to_live_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = SimpleNamespace(
        get=AsyncMock(return_value={"metadata": {"sandbox_id": "sandbox-live"}})
    )

    monkeypatch.setattr(
        "agent.utils.sandbox_state.get_config",
        lambda: {"metadata": {}},
    )
    monkeypatch.setattr(
        "agent.utils.sandbox_state.get_client",
        lambda: SimpleNamespace(threads=threads),
    )

    assert await get_sandbox_id_from_metadata("thread-1") == "sandbox-live"
    threads.get.assert_awaited_once_with("thread-1")
