"""MCP groups: both scopes in one run, one handshake per connection, edits applied per call."""

import hashlib
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from agent.dashboard import mcp_connections as mc
from agent.tool_loaders import mcp as loader


def _record(owner: str, name: str, tools: list[str], allowed: list[str] | None = None) -> dict:
    return {
        "id": hashlib.sha256(name.encode()).hexdigest()[:32],
        "owner": owner,
        "name": name,
        "url": f"https://{name}.example/mcp",
        "transport": "streamable_http",
        "enabled": True,
        "auth_type": "none",
        "headers": {},
        "revision": "r1",
        "version": "v1",
        "tools": [{"name": tool, "description": ""} for tool in tools],
        "allowed_tools": allowed,
    }


@pytest.fixture
def records(monkeypatch):
    stored = {
        mc.WORKSPACE_OWNER: [
            _record(mc.WORKSPACE_OWNER, "incident", ["list", "delete"], allowed=["list"]),
            _record(mc.WORKSPACE_OWNER, "unlisted", ["ping"]),
        ],
        "alice": [_record("alice", "Notion", ["search", "fetch"])],
    }

    async def list_records(owner):
        return stored.get(owner, [])

    async def get_record(owner, connection_id):
        return next(record for record in stored[owner] if record["id"] == connection_id)

    async def connection_config(owner, connection_id):
        return {
            "transport": "streamable_http",
            "url": (await get_record(owner, connection_id))["url"],
        }

    monkeypatch.setattr(loader, "list_records", list_records)
    monkeypatch.setattr(loader, "get_record", get_record)
    monkeypatch.setattr(loader, "connection_config", connection_config)
    loader._auth_failures.clear()
    return stored


class _Session:
    opened: list[_Session] = []

    def __init__(self, connection: dict[str, Any]) -> None:
        self.connection = connection
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.fail = False
        _Session.opened.append(self)

    async def initialize(self) -> None:
        pass

    async def list_tools(self, params=None):
        return SimpleNamespace(
            tools=[
                Tool(name=name, description=f"{name} tool", inputSchema={"type": "object"})
                for name in ("search", "fetch", "list", "delete", "ping")
            ],
            nextCursor=None,
        )

    async def call_tool(self, name, arguments, **kwargs):
        if self.fail:
            raise ConnectionError("gone")
        self.calls.append((name, arguments))
        return CallToolResult(
            content=[TextContent(type="text", text=f"{name}:{sorted(arguments)}")]
        )


@pytest.fixture
def sessions(monkeypatch):
    _Session.opened = []

    @asynccontextmanager
    async def create_session(connection, **kwargs):
        session = _Session(connection)
        try:
            yield session
        finally:
            session.closed = True

    monkeypatch.setattr(loader, "create_session", create_session)
    return _Session.opened


async def test_groups_cover_both_scopes_and_honour_allowlists(records, sessions):
    async with AsyncExitStack() as stack:
        groups = await loader.load_mcp_groups("alice", stack=stack, reserved_groups=["Browser"])
    assert set(groups) == {"incident", "Notion"}, "an unlisted workspace connection runs nothing"
    assert [name.split("_")[2] for name in groups["incident"].tool_names] == ["list"]
    assert sorted(name.split("_")[2] for name in groups["Notion"].tool_names) == ["fetch", "search"]
    assert (await loader.load_mcp_groups(None, stack=None)).keys() == {"incident"}


async def test_one_session_per_connection_per_run_and_reconnect_after_failure(records, sessions):
    async with AsyncExitStack() as stack:
        groups = await loader.load_mcp_groups("alice", stack=stack)
        tools = {tool.name: tool for tool in await groups["Notion"].load()}
        search = next(tool for name, tool in tools.items() if "_search_" in name)
        fetch = next(tool for name, tool in tools.items() if "_fetch_" in name)
        assert len(sessions) == 1, "loading the catalog opened the run's session"
        await search.ainvoke({"query": "a"})
        await fetch.ainvoke({"id": "1"})
        assert len(sessions) == 1, "calls reuse the session instead of re-handshaking"
        assert [call[0] for call in sessions[0].calls] == ["search", "fetch"]
        sessions[0].fail = True
        result = await search.ainvoke({"query": "b"})
        assert "unavailable" in str(result)
        assert sessions[0].closed, "a failed call drops the broken session"
        await fetch.ainvoke({"id": "2"})
        assert len(sessions) == 2 and sessions[1].calls == [("fetch", {"id": "2"})]
        assert not sessions[1].closed
    assert sessions[1].closed, "the run's exit closes what it opened"
