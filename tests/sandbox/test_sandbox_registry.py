import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import SandboxBackendProtocol
from support.sandbox_fakes import FakeSandboxBackend

from agent.utils.sandbox_registry import (
    SANDBOX_BACKENDS,
    clear_sandbox_backend,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_backend,
    get_sandbox_id_from_metadata,
    set_sandbox_backend,
)


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
        return FakeSandboxBackend()

    monkeypatch.setattr(
        "agent.utils.sandbox_registry.get_sandbox_id_from_metadata",
        get_sandbox_id_from_metadata,
    )
    monkeypatch.setattr("agent.utils.sandbox_registry.create_sandbox", create_sandbox)

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
        return FakeSandboxBackend()

    async def create_sandbox(sandbox_id: str):
        raise AssertionError(f"unexpected direct reconnect to {sandbox_id}")

    monkeypatch.setattr("agent.utils.sandbox_registry.create_sandbox", create_sandbox)

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
async def test_missing_sandbox_id_in_metadata_fails_the_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "thread-1"
    clear_sandbox_backend(thread_id)

    async def no_sandbox_id(requested_thread_id: str) -> None:
        return None

    monkeypatch.setattr("agent.utils.sandbox_registry.get_sandbox_id_from_metadata", no_sandbox_id)

    proxy = get_or_create_sandbox_backend_proxy(thread_id)

    with pytest.raises(ValueError, match="Missing sandbox_id in thread metadata for thread-1"):
        await proxy.ready()
    clear_sandbox_backend(thread_id)


@pytest.mark.asyncio
async def test_get_sandbox_backend_awaits_registered_startup() -> None:
    thread_id = "thread-1"
    clear_sandbox_backend(thread_id)
    release = asyncio.Event()

    async def reconnect():
        await release.wait()
        return FakeSandboxBackend()

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


def test_set_sandbox_backend_replaces_the_backend_behind_the_thread_handle() -> None:
    thread_id = "thread-1"
    clear_sandbox_backend(thread_id)
    first = cast(SandboxBackendProtocol, FakeSandboxBackend("sandbox-1"))
    second = cast(SandboxBackendProtocol, FakeSandboxBackend("sandbox-2"))

    proxy = set_sandbox_backend(thread_id, first)
    proxy.cache_work_dir("/workspace")

    assert set_sandbox_backend(thread_id, second) is proxy
    assert proxy.current is second
    assert proxy.work_dir is None
    clear_sandbox_backend(thread_id)


def test_clear_sandbox_backend_drops_the_thread_handle() -> None:
    thread_id = "thread-1"
    set_sandbox_backend(thread_id, cast(SandboxBackendProtocol, FakeSandboxBackend()))

    clear_sandbox_backend(thread_id)

    assert thread_id not in SANDBOX_BACKENDS


@pytest.mark.asyncio
async def test_sandbox_id_metadata_falls_back_to_live_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = SimpleNamespace(
        get=AsyncMock(return_value={"metadata": {"sandbox_id": "sandbox-live"}})
    )

    monkeypatch.setattr(
        "agent.utils.sandbox_registry.get_config",
        lambda: {"metadata": {}},
    )
    monkeypatch.setattr(
        "agent.utils.sandbox_registry.get_client",
        lambda: SimpleNamespace(threads=threads),
    )

    assert await get_sandbox_id_from_metadata("thread-1") == "sandbox-live"
    threads.get.assert_awaited_once_with("thread-1")
