"""What the agent sees when its sandbox is the machine running the CLI.

The "CLI" here is a coroutine doing exactly what the real one does over HTTP:
claim the request, run it, post the answer back.
"""

import asyncio
import base64
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text

from agent.bridge.backend import BridgeSandboxBackend
from agent.bridge.protocol import JsonObject
from agent.bridge.store import Bridge, BridgeStore, ClaimedRequest
from agent.database import postgres
from agent.sandboxes.state import SandboxUnreachableError

OWNER = "test-user"
THREAD_ID = "thread-1"

Reply = Callable[[ClaimedRequest], JsonObject]


async def _open() -> Bridge:
    bridge = await BridgeStore.register(
        owner_login=OWNER,
        hostname="laptop.local",
        root_path="/Users/test/project",
        label=None,
        bridge_id=None,
    )
    assert bridge is not None
    return bridge


def _cli(
    bridge_id: str, *, result: Reply | None = None, error: str | None = None
) -> Awaitable[ClaimedRequest]:
    """A stand-in CLI that answers the next request on ``bridge_id`` and stops."""

    async def run() -> ClaimedRequest:
        for _ in range(500):
            claimed = await BridgeStore.claim(bridge_id, limit=8)
            if claimed:
                request = claimed[0]
                await BridgeStore.complete(
                    bridge_id,
                    request.request_id,
                    result=None if result is None else result(request),
                    error=error,
                )
                return request
            await asyncio.sleep(0.01)
        raise AssertionError("no bridge request arrived")

    return asyncio.create_task(run())


async def test_execute_round_trips_through_the_queue(registry_db: None) -> None:
    bridge = await _open()
    backend = BridgeSandboxBackend(bridge_id=bridge.bridge_id, thread_id=THREAD_ID)
    cli = _cli(
        bridge.bridge_id,
        result=lambda _request: {"output": "hi\n", "exit_code": 0, "truncated": False},
    )

    response = await backend.aexecute("echo hi", timeout=5)

    assert response.output == "hi\n"
    assert response.exit_code == 0
    request = await cli
    assert request.method == "execute"
    assert request.params == {"command": "echo hi", "timeout": 5}
    assert backend.id == f"bridge:{bridge.bridge_id}"


async def test_download_decodes_the_bytes_the_cli_sent(registry_db: None) -> None:
    bridge = await _open()
    backend = BridgeSandboxBackend(bridge_id=bridge.bridge_id, thread_id=THREAD_ID)
    cli = _cli(
        bridge.bridge_id,
        result=lambda _request: {
            "responses": [
                {
                    "path": "/Users/test/project/README.md",
                    "content_base64": base64.b64encode(b"# hello").decode(),
                    "error": None,
                },
                {"path": "/Users/test/project/missing", "error": "file_not_found"},
            ]
        },
    )

    responses = await backend.adownload_files(
        ["/Users/test/project/README.md", "/Users/test/project/missing"]
    )

    assert responses[0].content == b"# hello"
    assert responses[1].content is None
    assert responses[1].error == "file_not_found"
    request = await cli
    assert request.params == {
        "paths": ["/Users/test/project/README.md", "/Users/test/project/missing"]
    }


async def test_an_error_reply_surfaces_as_an_exception(registry_db: None) -> None:
    bridge = await _open()
    backend = BridgeSandboxBackend(bridge_id=bridge.bridge_id, thread_id=THREAD_ID)
    cli = _cli(bridge.bridge_id, error="the CLI lost its shell")

    with pytest.raises(RuntimeError, match="the CLI lost its shell"):
        await backend.aexecute("echo hi", timeout=5)

    await cli


async def test_a_bridge_that_stopped_heartbeating_is_unreachable(registry_db: None) -> None:
    bridge = await _open()
    async with postgres.transaction() as conn:
        await conn.execute(
            text(
                """
                UPDATE sandbox_bridge
                SET last_heartbeat_at = clock_timestamp() - make_interval(secs => 600)
                WHERE bridge_id = :bridge_id
                """
            ),
            {"bridge_id": bridge.bridge_id},
        )

    with pytest.raises(SandboxUnreachableError):
        await BridgeSandboxBackend.connect(THREAD_ID, bridge.bridge_id)

    backend = BridgeSandboxBackend(bridge_id=bridge.bridge_id, thread_id=THREAD_ID)
    with pytest.raises(SandboxUnreachableError):
        await backend.aexecute("echo hi", timeout=5)


async def test_closing_the_bridge_ends_a_waiting_request(registry_db: None) -> None:
    bridge = await _open()
    backend = BridgeSandboxBackend(bridge_id=bridge.bridge_id, thread_id=THREAD_ID)

    async def close_once_claimed() -> None:
        for _ in range(500):
            if await BridgeStore.claim(bridge.bridge_id, limit=8):
                await BridgeStore.close(bridge.bridge_id, owner_login=OWNER)
                return
            await asyncio.sleep(0.01)
        raise AssertionError("no bridge request arrived")

    closer = asyncio.create_task(close_once_claimed())
    with pytest.raises(RuntimeError, match="bridge closed"):
        await backend.aexecute("sleep 60", timeout=5)
    await closer


async def test_sync_methods_are_refused() -> None:
    backend = BridgeSandboxBackend(bridge_id="none", thread_id=THREAD_ID)

    with pytest.raises(NotImplementedError):
        backend.execute("echo hi")
    with pytest.raises(NotImplementedError):
        backend.upload_files([("/tmp/a", b"a")])
    with pytest.raises(NotImplementedError):
        backend.download_files(["/tmp/a"])
