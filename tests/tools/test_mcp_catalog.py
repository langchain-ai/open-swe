import asyncio
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from langgraph_api import cache
from mcp.types import Tool

from agent.mcp import MCPConnectionUpdate, runtime
from agent.mcp import workspace as settings
from agent.tool_loaders import workspace_mcp as loader

SEARCH = Tool(name="search", inputSchema={"type": "object"})
FETCH = Tool(name="fetch", inputSchema={"type": "object"})


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    if hasattr(cache, "_CACHE"):
        cache._CACHE.clear()


async def save(**kwargs):
    await settings.save_workspace_mcp(
        "default",
        "example",
        MCPConnectionUpdate(name="example", url="https://example.com/mcp", **kwargs),
    )


async def names() -> list[str]:
    tools = await loader.load_workspace_mcp_tools("default")
    return [(tool.metadata or {})["mcp_tool_name"] for tool in tools]


async def test_distributed_catalog_skips_discovery(fake_store, monkeypatch):
    await save(allowed_tools=["search"])
    discover = AsyncMock(return_value=[SEARCH])
    monkeypatch.setattr(runtime, "_discover_tools", discover)

    assert await names() == ["search"]
    assert await names() == ["search"]
    assert discover.await_count == 1


async def test_cache_failure_does_not_start_independent_discovery(fake_store, monkeypatch):
    await save(allowed_tools=["search"])
    discover = AsyncMock(return_value=[SEARCH])
    monkeypatch.setattr(runtime, "_discover_tools", discover)
    monkeypatch.setattr(runtime, "swr", AsyncMock(side_effect=ConnectionError("cache unavailable")))

    assert await names() == []
    discover.assert_not_awaited()


async def test_changed_connection_rediscovers(fake_store, monkeypatch):
    await save(allowed_tools=["search", "fetch"])
    discover = AsyncMock(side_effect=[[SEARCH], [SEARCH, FETCH]])
    monkeypatch.setattr(runtime, "_discover_tools", discover)
    assert await names() == ["search"]

    await save(allowed_tools=["search", "fetch"])

    assert sorted(await names()) == ["fetch", "search"]


async def test_stale_catalog_is_served_then_refreshed(fake_store, monkeypatch):
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
