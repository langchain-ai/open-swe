import asyncio
from unittest.mock import AsyncMock

import httpx2
import pytest
from cryptography.fernet import Fernet
from mcp.types import Tool

from openswe.mcp import MCPConnection, MCPConnectionUpdate, runtime
from openswe.mcp import workspace as settings
from openswe.tool_loaders import workspace_mcp as loader

SEARCH = Tool(name="search", input_schema={"type": "object"})
FETCH = Tool(name="fetch", input_schema={"type": "object"})


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def save(**kwargs):
    await settings.save_workspace_mcp(
        "default",
        "example",
        MCPConnectionUpdate(name="example", url="https://example.com/mcp", **kwargs),
    )


async def names() -> list[str]:
    tools = await loader.load_workspace_mcp_tools("default")
    return [(tool.metadata or {})["mcp_tool_name"] for tool in tools]


async def test_changed_connection_rediscovers(registry_db, monkeypatch):
    await save(allowed_tools=["search", "fetch"])
    discover = AsyncMock(side_effect=[[SEARCH], [SEARCH, FETCH]])
    monkeypatch.setattr(runtime, "_discover_tools", discover)
    assert await names() == ["search"]

    await save(allowed_tools=["search", "fetch"])

    assert sorted(await names()) == ["fetch", "search"]


async def test_stale_catalog_is_served_then_refreshed(registry_db, monkeypatch):
    from langgraph_api import cache

    await save(allowed_tools=["search", "fetch"])
    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(return_value=[SEARCH]))
    assert await names() == ["search"]
    refreshed = asyncio.Event()

    async def discover(record, namespace):
        await refreshed.wait()
        return [SEARCH, FETCH]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    now = cache._now_ms
    monkeypatch.setattr(cache, "_now_ms", lambda: now() + 11 * 60 * 1000)

    assert await names() == ["search"]
    refreshed.set()
    await asyncio.gather(*(state.task for state in cache._SWR_STATES.values() if state.task))
    monkeypatch.setattr(cache, "_now_ms", now)
    assert sorted(await names()) == ["fetch", "search"]


@pytest.mark.parametrize("phase", ["miss", "stale", "expired", "mutate"])
async def test_discovery_limits_network_work_not_cache_hits(monkeypatch, phase):
    from langgraph_api import cache

    records = [
        MCPConnection.model_construct(
            name=f"server{i}",
            url="https://example.com/mcp",
            revision="one",
            allowed_tools=["search"],
        )
        for i in range(24)
    ]
    warm = records[0].model_copy(update={"name": "warm"})
    source = runtime.MCPSource(
        scope="workspace",
        namespace=("bounded", phase),
        list_connections=AsyncMock(return_value=[*records, warm]),
        get_connection=AsyncMock(
            side_effect=lambda name: next(r for r in records if r.name == name)
        ),
    )
    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(return_value=[SEARCH]))
    if phase != "miss":
        await asyncio.gather(*(runtime._catalog(source, record) for record in records))
    now = cache._now_ms()
    age = {"stale": 11 * 60 * 1000, "expired": 25 * 60 * 60 * 1000}.get(phase, 0)
    monkeypatch.setattr(cache, "_now_ms", lambda: now + age)
    await runtime._catalog(source, warm)
    saturated = asyncio.Event()
    release = asyncio.Event()
    active = peak = calls = 0

    async def discover(record, namespace):
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        if active == 8:
            saturated.set()
        try:
            await release.wait()
            return [SEARCH]
        finally:
            active -= 1

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    refreshes = (
        [
            asyncio.create_task(runtime.refresh_mcp_catalog(source, record.name))
            for record in records
        ]
        if phase == "mutate"
        else []
    )
    loads = [asyncio.create_task(runtime.load_mcp_tools(source)) for _ in range(2)]
    try:
        await asyncio.wait_for(saturated.wait(), timeout=2)
        tools = await asyncio.wait_for(
            runtime.load_mcp_tools(source, connection_name="warm"), timeout=2
        )
        assert [tool.name for tool in tools] == ["mcp_warm_search"]
        if phase == "stale":
            assert all(
                len(tools) == 25 for tools in await asyncio.wait_for(asyncio.gather(*loads), 2)
            )
    finally:
        release.set()
        results = await asyncio.gather(*loads, *refreshes)
        await asyncio.gather(*(state.task for state in cache._SWR_STATES.values() if state.task))
    assert all(len(tools) == 25 for tools in results[:2])
    assert (calls, peak, active) == (24, 8, 0)


async def test_cancelled_discovery_releases_slots(monkeypatch):
    monkeypatch.setattr(runtime, "_DISCOVERY_CONCURRENCY", 1)
    record = MCPConnection.model_construct(name="example", url="https://example.com/mcp")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def discover(record, namespace):
        entered.set()
        await release.wait()
        return [SEARCH]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    active = asyncio.create_task(runtime.discover_tools(record, ("ns",)))
    queued = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        queued = asyncio.create_task(runtime.discover_tools(record, ("ns",)))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        release.set()
        assert await asyncio.wait_for(runtime.discover_tools(record, ("ns",)), 2) == [SEARCH]
    finally:
        active.cancel()
        if queued is not None:
            queued.cancel()
        await asyncio.gather(active, *([queued] if queued else []), return_exceptions=True)


async def test_timed_out_discovery_does_not_block_queued_connection(monkeypatch):
    monkeypatch.setattr(runtime, "_DISCOVERY_CONCURRENCY", 1)
    monkeypatch.setattr(runtime, "_TIMEOUT_SECONDS", 0.01)
    slow = MCPConnection.model_construct(name="slow", url="https://example.com/mcp")
    healthy = slow.model_copy(update={"name": "healthy"})

    async def discover(record, namespace):
        if record.name == "slow":
            await asyncio.Event().wait()
        return [SEARCH]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    results = await asyncio.wait_for(
        asyncio.gather(
            runtime.discover_tools(slow, ("ns",)),
            runtime.discover_tools(healthy, ("ns",)),
            return_exceptions=True,
        ),
        2,
    )
    assert isinstance(results[0], ValueError)
    assert "timed out" in str(results[0])
    assert results[1] == [SEARCH]


@pytest.mark.parametrize(
    ("raised", "message"),
    [
        (
            ExceptionGroup("session", [httpx2.ConnectError("[Errno 8] nodename not known")]),
            "Could not reach the MCP server: [Errno 8] nodename not known",
        ),
        (
            RuntimeError("https://example.com/mcp?token=secret"),
            "Could not discover MCP tools (RuntimeError); check the URL and authentication headers",
        ),
    ],
)
async def test_discovery_error_names_the_failure_without_secrets(monkeypatch, raised, message):
    from openswe.mcp.models import MCPConnection

    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(side_effect=raised))
    record = MCPConnection.model_construct(name="example", url="https://example.com/mcp")

    with pytest.raises(ValueError) as error:
        await runtime.discover_tools(record, ("ns",))

    assert str(error.value) == message
