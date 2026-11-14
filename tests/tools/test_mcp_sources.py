import pytest
from mcp.types import CallToolResult, TextContent, Tool

from openswe.mcp import MCPConnection, runtime
from openswe.sandboxes.tool_runtime import ToolSurface
from tests.mcp_helpers import fake_mcp_server


def record(name="linear", **fields):
    return MCPConnection(
        **{
            "name": name,
            "url": f"https://{name}.example/mcp",
            "allowed_tools": ["search"],
            "revision": "same-revision",
            **fields,
        }
    )


def source(namespace, records):
    async def list_connections():
        return list(records.values())

    async def get_connection(name):
        return records.get(name)

    return runtime.MCPSource(namespace, list_connections, get_connection, scope="managed")


@pytest.fixture
def remote(monkeypatch):
    async def discover(record):
        return [
            Tool(name=name, description=record.url, input_schema={"type": "object"})
            for name in record.allowed_tools
        ]

    async def call(transport, name, arguments):
        return CallToolResult(content=[TextContent(type="text", text=transport.url)])

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    fake_mcp_server(monkeypatch, call=call)


async def test_sources_combine_distinct_connections_and_replace_matching_names(remote):
    shared = source(
        ("bot_kit",),
        {
            "linear": record(allowed_tools=["search", "delete"]),
            "incident": record("incident"),
        },
    )
    kit = source(
        ("managed_tools", "alice"),
        {"linear": record(url="https://personal.example/mcp")},
    )
    tools = await runtime.load_mcp_tools(shared, kit)
    assert len(tools) == 2
    assert len({tool.name for tool in tools}) == 2
    assert [(await tool.ainvoke({}))[0]["text"] for tool in tools] == [
        "https://incident.example/mcp",
        "https://personal.example/mcp",
    ]


@pytest.mark.parametrize("personal_override", [False, True])
async def test_each_tier_replaces_the_same_named_connection_from_the_tier_before(
    remote, personal_override
):
    shared = source(
        ("bot_kit", "oss"),
        {"linear": record(), "docs": record("docs"), "incident": record("incident")},
    )
    kit = source(
        ("managed_tools", "alice"),
        {
            "docs": record("docs", url="https://personal-docs.example/mcp"),
            **(
                {"linear": record(url="https://personal-linear.example/mcp")}
                if personal_override
                else {}
            ),
        },
    )
    tools = await runtime.load_mcp_tools(shared, kit)
    assert sorted([(await tool.ainvoke({}))[0]["text"] for tool in tools]) == sorted(
        [
            "https://personal-docs.example/mcp",
            "https://incident.example/mcp",
            "https://personal-linear.example/mcp"
            if personal_override
            else "https://linear.example/mcp",
        ]
    )
    catalog = ToolSurface(tools={tool.name: tool for tool in tools}).catalog()
    assert [entry.model_dump()["mcp_provenance"] for entry in catalog] == [
        {"connection_name": "docs", "scope": "managed", "overridden_scopes": ["managed"]},
        {"connection_name": "incident", "scope": "managed", "overridden_scopes": []},
        {
            "connection_name": "linear",
            "scope": "managed",
            "overridden_scopes": ["managed"] if personal_override else [],
        },
    ]


@pytest.mark.parametrize("override", [{"enabled": False}, {"allowed_tools": []}])
async def test_disabled_or_empty_override_never_falls_back_to_the_source_before(remote, override):
    shared = source(("bot_kit",), {"linear": record()})
    kit = source(("managed_tools", "alice"), {"linear": record(**override)})
    assert await runtime.load_mcp_tools(shared, kit) == []


async def test_catalogs_are_isolated_by_owner_even_with_identical_revisions(remote):
    alice = source(("managed_tools", "alice"), {"linear": record(allowed_tools=["search"])})
    bob = source(("managed_tools", "bob"), {"linear": record(allowed_tools=["delete"])})
    alice_tools = await runtime.load_mcp_tools(alice)
    bob_tools = await runtime.load_mcp_tools(bob)
    assert len(alice_tools) == len(bob_tools) == 1
    assert "search" in alice_tools[0].name
    assert "delete" in bob_tools[0].name


@pytest.mark.parametrize("had_override", [False, True])
async def test_loaded_tool_cannot_switch_scope_when_override_changes(remote, had_override):
    shared = source(("bot_kit",), {"linear": record()})
    personal = {"linear": record()} if had_override else {}
    kit = source(("managed_tools", "alice"), personal)
    tool = (await runtime.load_mcp_tools(shared, kit))[0]
    if had_override:
        personal.clear()
    else:
        personal["linear"] = record()
    assert "changed" in await tool.ainvoke({})


async def test_unavailable_source_does_not_expose_lower_precedence_tools(remote, caplog):
    shared = source(("bot_kit",), {"linear": record()})

    async def unavailable(*args):
        raise ValueError("private store details")

    kit = runtime.MCPSource(("managed_tools", "alice"), unavailable, unavailable, scope="managed")
    assert await runtime.load_mcp_tools(shared, kit) == []
    assert "private store details" not in caplog.text


async def test_failed_lookup_blocks_loaded_tool_without_falling_back(remote, caplog):
    shared = source(("bot_kit",), {"linear": record()})

    async def list_connections():
        return [record()]

    async def unavailable(name):
        raise ValueError("private store details")

    kit = runtime.MCPSource(
        ("managed_tools", "alice"), list_connections, unavailable, scope="managed"
    )
    tool = (await runtime.load_mcp_tools(shared, kit))[0]
    assert "MCP call failed" in await tool.ainvoke({})
    assert "private store details" not in caplog.text
