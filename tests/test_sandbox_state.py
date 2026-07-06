from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.backends.protocol import ExecuteResponse

from agent.utils.sandbox_state import (
    SANDBOX_BACKENDS,
    clear_sandbox_backend,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_id_from_metadata,
)


class _FakeSandboxBackend:
    id = "sandbox-1"

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(output=f"{self.id}: {command}: {timeout}", exit_code=0)


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
