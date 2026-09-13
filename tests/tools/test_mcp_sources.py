from contextlib import asynccontextmanager

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from agent.mcp import MCPConnection, runtime


def record(name="linear", **fields):
    return MCPConnection(
        **{
            "name": name,
            "url": f"https://{name}.example/mcp",
            "allowed_tools": ["search"],
            "revision": "same-revision",
            "updated_at": "2026-09-09T00:00:00Z",
            **fields,
        }
    )


def source(namespace, records):
    async def list_connections():
        return list(records.values())

    async def get_connection(name):
        return records.get(name)

    return runtime.MCPSource(namespace, list_connections, get_connection)


@pytest.fixture
def remote(monkeypatch):
    async def discover(record, namespace):
        return [
            Tool(name=name, description=record.url, inputSchema={"type": "object"})
            for name in record.allowed_tools
        ]

    class Session:
        def __init__(self, url):
            self.url = url

        async def initialize(self):
            pass

        async def call_tool(self, name, arguments, **kwargs):
            return CallToolResult(content=[TextContent(type="text", text=self.url)])

    @asynccontextmanager
    async def session(connection, **kwargs):
        yield Session(connection["url"])

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    monkeypatch.setattr("langchain_mcp_adapters.tools.create_session", session)


async def test_sources_combine_distinct_connections_and_replace_matching_names(remote):
    workspace = source(
        ("workspace_mcps",),
        {
            "linear": record(allowed_tools=["search", "delete"]),
            "incident": record("incident"),
        },
    )
    user = source(
        ("user_mcps", "alice"),
        {"linear": record(url="https://personal.example/mcp")},
    )
    tools = await runtime.load_mcp_tools(workspace, user)
    assert len(tools) == 2
    assert len({tool.name for tool in tools}) == 2
    assert [(await tool.ainvoke({}))[0]["text"] for tool in tools] == [
        "https://incident.example/mcp",
        "https://personal.example/mcp",
    ]


@pytest.mark.parametrize("override", [{"enabled": False}, {"allowed_tools": []}])
async def test_disabled_or_empty_override_never_falls_back_to_workspace(remote, override):
    workspace = source(("workspace_mcps",), {"linear": record()})
    user = source(("user_mcps", "alice"), {"linear": record(**override)})
    assert await runtime.load_mcp_tools(workspace, user) == []


async def test_catalogs_are_isolated_by_owner_even_with_identical_revisions(remote):
    alice = source(("user_mcps", "alice"), {"linear": record(allowed_tools=["search"])})
    bob = source(("user_mcps", "bob"), {"linear": record(allowed_tools=["delete"])})
    alice_tools = await runtime.load_mcp_tools(alice)
    bob_tools = await runtime.load_mcp_tools(bob)
    assert len(alice_tools) == len(bob_tools) == 1
    assert "search" in alice_tools[0].name
    assert "delete" in bob_tools[0].name


@pytest.mark.parametrize("had_override", [False, True])
async def test_loaded_tool_cannot_switch_scope_when_override_changes(remote, had_override):
    workspace = source(("workspace_mcps",), {"linear": record()})
    personal = {"linear": record()} if had_override else {}
    user = source(("user_mcps", "alice"), personal)
    tool = (await runtime.load_mcp_tools(workspace, user))[0]
    if had_override:
        personal.clear()
    else:
        personal["linear"] = record()
    assert "changed" in await tool.ainvoke({})


async def test_unavailable_source_does_not_expose_lower_precedence_tools(remote, caplog):
    workspace = source(("workspace_mcps",), {"linear": record()})

    async def unavailable(*args):
        raise ValueError("private store details")

    user = runtime.MCPSource(("user_mcps", "alice"), unavailable, unavailable)
    assert await runtime.load_mcp_tools(workspace, user) == []
    assert "private store details" not in caplog.text


async def test_failed_lookup_blocks_loaded_tool_without_falling_back(remote, caplog):
    workspace = source(("workspace_mcps",), {"linear": record()})

    async def list_connections():
        return [record()]

    async def unavailable(name):
        raise ValueError("private store details")

    user = runtime.MCPSource(("user_mcps", "alice"), list_connections, unavailable)
    tool = (await runtime.load_mcp_tools(workspace, user))[0]
    assert "MCP call failed" in await tool.ainvoke({})
    assert "private store details" not in caplog.text
