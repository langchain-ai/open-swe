import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.dashboard import routes
from agent.dashboard.threads import access, proxy
from agent.sandboxes.providers import langsmith


@pytest.mark.parametrize("login", ["owner", "viewer"])
async def test_stream_rechecks_visibility_before_delivery(monkeypatch, login):
    metadata = {"source": "dashboard", "owner_login": "owner"}
    monkeypatch.setattr(
        access,
        "langgraph_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": metadata}))
        ),
    )

    async def upstream():
        yield b"public"
        metadata["visibility"] = "private"
        yield b"private"

    stream = access.authorized_thread_stream(upstream(), "thread", login)
    assert await anext(stream) == b"public"
    if login == "owner":
        assert await anext(stream) == b"private"
    else:
        with pytest.raises(HTTPException):
            await anext(stream)
    await stream.aclose()


async def test_idle_stream_revocation_cancels_upstream(monkeypatch):
    waiting = asyncio.Event()
    closed = asyncio.Event()
    revoked = False

    async def authorize(*args, **kwargs):
        if revoked:
            raise HTTPException(404)
        return {}

    async def upstream():
        try:
            waiting.set()
            await asyncio.Event().wait()
            yield b"never"
        finally:
            closed.set()

    monkeypatch.setattr(access, "_ACCESS_RECHECK_SECONDS", 0.001)
    monkeypatch.setattr(access, "_readable_thread_metadata", authorize)
    monkeypatch.setattr(proxy, "_readable_thread_metadata", authorize)
    monkeypatch.setattr(proxy, "stream_thread_events", lambda *args: upstream())
    stream = await proxy.proxy_dashboard_thread_stream_events("thread", "viewer", b"{}")
    read = asyncio.create_task(anext(stream, None))
    await asyncio.wait_for(waiting.wait(), 1)
    revoked = True
    assert await asyncio.wait_for(read, 1) is None
    assert closed.is_set()


@pytest.mark.parametrize(
    "message", [{"type": "input", "data": "secret"}, {"type": "resize", "cols": 80, "rows": 24}]
)
async def test_terminal_revokes_before_forwarding_input(monkeypatch, message):
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.setattr(routes, "_CLOUD_TERMINAL_SLOTS", asyncio.Semaphore(1))
    monkeypatch.setattr(
        routes,
        "get_dashboard_terminal_sandbox",
        AsyncMock(side_effect=[("sandbox", "repo"), HTTPException(404)]),
    )

    class Handle:
        pid = 123
        send_input = AsyncMock()
        kill = AsyncMock()

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

    handle = Handle()
    sandbox = SimpleNamespace(run=AsyncMock(side_effect=[SimpleNamespace(success=True), handle]))
    client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(
        langsmith, "connect_async_langsmith_sandbox", AsyncMock(return_value=(client, sandbox))
    )
    websocket = SimpleNamespace(
        accept=AsyncMock(),
        close=AsyncMock(),
        send_text=AsyncMock(),
        receive_json=AsyncMock(return_value=message),
    )
    await asyncio.wait_for(routes._cloud_terminal(websocket, "thread", {"sub": "viewer"}), 1)
    handle.send_input.assert_not_awaited()
    assert sandbox.run.await_count == 2
    websocket.close.assert_awaited_once_with(code=1008, reason="Thread access revoked")
    handle.kill.assert_awaited_once()
    client.aclose.assert_awaited_once()
