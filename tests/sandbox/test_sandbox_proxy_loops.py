import asyncio
from typing import Any
from unittest.mock import MagicMock

from agent.utils.sandbox_state import SandboxBackendProxy


def _run_on_new_loop(coro_factory: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


def test_proxy_ready_survives_isolated_loops() -> None:
    """BG_JOB_ISOLATED_LOOPS gives each run its own loop; the cached proxy is shared."""
    backend = MagicMock()

    async def reconnect() -> Any:
        await asyncio.sleep(0)
        return backend

    proxy = SandboxBackendProxy(thread_id="t1", reconnect=reconnect)

    assert _run_on_new_loop(proxy.ready) is backend
    assert _run_on_new_loop(proxy.ready) is backend


def test_proxy_discards_pending_startup_from_a_dead_loop() -> None:
    backend = MagicMock()

    async def reconnect() -> Any:
        await asyncio.sleep(0)
        return backend

    proxy = SandboxBackendProxy(thread_id="t1", reconnect=reconnect)

    loop_a = asyncio.new_event_loop()
    loop_a.run_until_complete(_seed_pending_startup(proxy, reconnect))

    try:
        assert _run_on_new_loop(proxy.ready) is backend
    finally:
        loop_a.run_until_complete(asyncio.sleep(0))
        loop_a.close()


async def _seed_pending_startup(proxy: SandboxBackendProxy, reconnect: Any) -> None:
    proxy._loop = asyncio.get_running_loop()
    proxy._lock = asyncio.Lock()
    proxy._startup_task = asyncio.create_task(reconnect())
