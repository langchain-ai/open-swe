import pytest
from mcp.types import CallToolResult, TextContent, Tool
from runtime_api.connections import ConnectionRef

from openswe.mcp import MCPConnection, runtime
from openswe.mcp.models import MCPScope
from openswe.sandboxes.tool_runtime import ToolSurface
from tests.mcp_helpers import fake_mcp_server


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

    scopes: dict[str, MCPScope] = {
        "workspace_mcps": "workspace",
        "user_mcps": "user",
    }
    return runtime.MCPSource(
        namespace, list_connections, get_connection, scope=scopes[namespace[0]]
    )


@pytest.fixture
def remote(monkeypatch):
    async def discover(record, namespace):
        return [
            Tool(name=name, description=record.url, input_schema={"type": "object"})
            for name in record.allowed_tools
        ]

    async def call(transport, name, arguments):
        return CallToolResult(content=[TextContent(type="text", text=transport.url)])

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    fake_mcp_server(monkeypatch, call=call)


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


@pytest.mark.parametrize("personal_override", [False, True])
async def test_each_tier_replaces_the_same_named_connection_from_the_tier_before(
    remote, personal_override
):
    workspace = source(
        ("workspace_mcps", "oss"),
        {"linear": record(), "docs": record("docs"), "incident": record("incident")},
    )
    user = source(
        ("user_mcps", "alice"),
        {
            "docs": record("docs", url="https://personal-docs.example/mcp"),
            **(
                {"linear": record(url="https://personal-linear.example/mcp")}
                if personal_override
                else {}
            ),
        },
    )
    tools = await runtime.load_mcp_tools(workspace, user)
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
        {"connection_name": "docs", "scope": "user", "overridden_scopes": ["workspace"]},
        {"connection_name": "incident", "scope": "workspace", "overridden_scopes": []},
        {
            "connection_name": "linear",
            "scope": "user" if personal_override else "workspace",
            "overridden_scopes": ["workspace"] if personal_override else [],
        },
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

    user = runtime.MCPSource(("user_mcps", "alice"), unavailable, unavailable, scope="user")
    assert await runtime.load_mcp_tools(workspace, user) == []
    assert "private store details" not in caplog.text


async def test_failed_lookup_blocks_loaded_tool_without_falling_back(remote, caplog):
    workspace = source(("workspace_mcps",), {"linear": record()})

    async def list_connections():
        return [record()]

    async def unavailable(name):
        raise ValueError("private store details")

    user = runtime.MCPSource(("user_mcps", "alice"), list_connections, unavailable, scope="user")
    tool = (await runtime.load_mcp_tools(workspace, user))[0]
    assert "MCP call failed" in await tool.ainvoke({})
    assert "private store details" not in caplog.text


async def test_only_linears_mcp_server_reads_the_linear_channels_app_token(remote):
    linear_token = ConnectionRef(slug="mda/linear-token", owner="agent")
    workspace = source(
        ("workspace_mcps",),
        {
            "linear": record(url="https://mcp.linear.app/mcp", connection=linear_token),
            # Saved before the rule, or past it: the token never goes elsewhere.
            "lookalike": record(
                "lookalike", url="https://mcp.linear.app.evil.com/mcp", connection=linear_token
            ),
            "github": record(
                "github",
                url="https://mcp.linear.app/mcp",
                connection=ConnectionRef(slug="mda/github-token", owner="agent"),
            ),
        },
    )
    loaded = await runtime.load_mcp_tools(workspace)
    assert [(server.name, server.url) for server in loaded.runtime_servers] == [
        ("linear", "https://mcp.linear.app/mcp")
    ]


@pytest.mark.parametrize("personal_connection", [False, True])
async def test_a_server_read_through_an_mda_connection_is_the_mda_runs_by_tier_precedence(
    remote, personal_connection
):
    workspace = source(
        ("workspace_mcps",),
        {
            "linear": record(connection=ConnectionRef(slug="open-swe-mcp-linear", owner="agent")),
            # A shared server never reads a person's own connection.
            "notes": record(
                "notes", connection=ConnectionRef(slug="open-swe-mcp-notes", owner="user")
            ),
            # Nor a credential Open SWE or a channel reads for itself, saved before the rule.
            "github": record(
                "github", connection=ConnectionRef(slug="mda/github-token", owner="agent")
            ),
        },
    )
    user = source(
        ("user_mcps", "alice"),
        {
            "linear": record(
                url="https://personal.example/mcp",
                connection=ConnectionRef(slug="open-swe-mcp-linear", owner="user")
                if personal_connection
                else None,
            )
        },
    )
    loaded = await runtime.load_mcp_tools(workspace, user)
    if personal_connection:
        assert loaded == []
        assert [
            (server.url, server.connection.owner, server.tools) for server in loaded.runtime_servers
        ] == [("https://personal.example/mcp", "user", {"search": "mcp_linear_search"})]
    else:
        assert [(await tool.ainvoke({}))[0]["text"] for tool in loaded] == [
            "https://personal.example/mcp"
        ]
        assert loaded.runtime_servers == []
