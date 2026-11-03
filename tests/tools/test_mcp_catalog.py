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


async def test_discovery_caps_active_requests(monkeypatch):
    active = peak = 0

    async def discover(record, namespace):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            return [SEARCH]
        finally:
            active -= 1

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    record = MCPConnection.model_construct(name="example", url="https://example.com/mcp")
    results = await asyncio.wait_for(
        asyncio.gather(*(runtime.discover_tools(record, ("ns",)) for _ in range(24))), 2
    )
    assert results == [[SEARCH]] * 24
    assert peak == 8


async def test_discovery_deadline_includes_waiting_for_capacity(monkeypatch):
    monkeypatch.setattr(runtime, "_DISCOVERY_CONCURRENCY", 1)
    monkeypatch.setattr(runtime, "_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(return_value=[SEARCH]))
    record = MCPConnection.model_construct(name="example", url="https://example.com/mcp")
    async with runtime._discovery_slots():
        with pytest.raises(ValueError, match="discovery timed out"):
            await asyncio.wait_for(runtime.discover_tools(record, ("ns",)), 1)
    assert await runtime.discover_tools(record, ("ns",)) == [SEARCH]


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
