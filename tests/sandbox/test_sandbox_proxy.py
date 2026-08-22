import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from deepagents.backends.protocol import (
    ExecuteOffloadResult,
    ExecuteResponse,
    SandboxBackendProtocol,
)
from deepagents.backends.sandbox import BaseSandbox
from support.sandbox_fakes import FakeSandboxBackend

from agent.sandboxes.proxy import SandboxBackendProxy, unwrap_sandbox_backend


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
    proxy = SandboxBackendProxy(cast(SandboxBackendProtocol, FakeSandboxBackend()), thread_id="t")

    result = await proxy.aexecute_with_offload("cmd", "/capture/path", max_inline_bytes=80_000)

    assert result.offloaded is False
    assert result.response.output == "sandbox-1: cmd: None"


@pytest.mark.asyncio
async def test_sandbox_proxy_reconnects_once_under_concurrent_calls() -> None:
    reconnected: list[str] = []

    async def reconnect():
        reconnected.append("thread-1")
        await asyncio.sleep(0)
        return FakeSandboxBackend()

    proxy = SandboxBackendProxy(
        thread_id="thread-1",
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
    )

    results = await asyncio.gather(*(proxy.aexecute(f"cmd-{idx}") for idx in range(5)))

    assert reconnected == ["thread-1"]
    assert [result.output for result in results] == [
        "sandbox-1: cmd-0: None",
        "sandbox-1: cmd-1: None",
        "sandbox-1: cmd-2: None",
        "sandbox-1: cmd-3: None",
        "sandbox-1: cmd-4: None",
    ]
    assert proxy.current.id == "sandbox-1"


@pytest.mark.asyncio
async def test_sandbox_proxy_publishes_itself_once_connected() -> None:
    published: list[SandboxBackendProxy] = []

    async def reconnect():
        return FakeSandboxBackend()

    proxy = SandboxBackendProxy(
        thread_id="thread-1",
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
        publish=published.append,
    )

    assert published == []
    await proxy.ready()
    assert published == [proxy]


@pytest.mark.asyncio
async def test_sandbox_proxy_without_reconnect_refuses_to_start() -> None:
    proxy = SandboxBackendProxy(thread_id="thread-1")

    with pytest.raises(RuntimeError, match="Cannot start sandbox without a reconnect callback"):
        await proxy.ready()


async def test_sandbox_proxy_drops_the_cached_work_dir_with_its_backend() -> None:
    proxy = SandboxBackendProxy(
        cast(SandboxBackendProtocol, FakeSandboxBackend()), thread_id="thread-1"
    )
    proxy.cache_work_dir("/workspace")

    assert proxy.work_dir == "/workspace"

    proxy.replace_backend(cast(SandboxBackendProtocol, FakeSandboxBackend("sandbox-2")))

    assert proxy.work_dir is None


@pytest.mark.asyncio
async def test_sandbox_proxy_refreshes_initialized_backend_when_started() -> None:
    refreshed = FakeSandboxBackend()
    calls = 0

    async def reconnect():
        nonlocal calls
        calls += 1
        return refreshed

    proxy = SandboxBackendProxy(
        cast(SandboxBackendProtocol, FakeSandboxBackend()),
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
        return FakeSandboxBackend()

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
        return FakeSandboxBackend()

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
        return FakeSandboxBackend()

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
        return FakeSandboxBackend()

    proxy = SandboxBackendProxy(
        thread_id="thread-1",
        reconnect=cast(Callable[[], Awaitable[SandboxBackendProtocol]], reconnect),
    )

    result = await proxy.adelete("/workspace/file.txt")

    assert result.path == "/workspace/file.txt"


def test_unwrap_sandbox_backend_returns_the_live_backend() -> None:
    backend = cast(SandboxBackendProtocol, FakeSandboxBackend())
    proxy = SandboxBackendProxy(backend, thread_id="thread-1")

    assert unwrap_sandbox_backend(proxy) is backend
    assert unwrap_sandbox_backend(backend) is backend
