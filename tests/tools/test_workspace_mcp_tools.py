import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from agent import server
from agent.dashboard import workspace_mcps as settings
from agent.mcp import MCPConnectionUpdate, load_mcp_tools, runtime
from agent.middleware.dynamic_tools import DynamicToolMiddleware
from agent.tool_loaders import workspace_mcp as loader
from agent.utils import ttl_cache


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def save(name="example", **kwargs):
    return await settings.save_workspace_mcp(
        name, MCPConnectionUpdate(name=name, url="https://example.com/mcp", **kwargs)
    )


async def test_generic_tools_are_namespaced_filtered_and_refresh_credentials(
    fake_store, monkeypatch
):
    await save(headers={"Authorization": "old"}, allowed_tools=["search"])
    definitions = [
        Tool(
            name=name,
            description=name,
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "runtime": {"type": "string"}},
                "required": ["query"],
            },
        )
        for name in ["search", "delete"]
    ]
    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(return_value=definitions))
    tools = await load_mcp_tools(settings.workspace_mcp_source)
    assert [tool.name for tool in tools] == ["mcp_example_search_8245e54055"]
    calls = []

    class Session:
        async def initialize(self):
            pass

        async def call_tool(self, name, arguments, **kwargs):
            calls.append((name, arguments))
            return CallToolResult(content=[TextContent(type="text", text="found")])

    @asynccontextmanager
    async def session(connection, **kwargs):
        assert connection["headers"] == {"X-Api-Key": "rotated"}
        assert "httpx_client_factory" in connection
        yield Session()

    monkeypatch.setattr("langchain_mcp_adapters.tools.create_session", session)
    await save(headers={"X-Api-Key": "rotated"}, allowed_tools=["search"])
    result = await tools[0].ainvoke({"query": "incident", "runtime": "python"})
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "found"
    assert calls == [("search", {"query": "incident", "runtime": "python"})]
    await save(enabled=False)
    assert "disabled" in await tools[0].ainvoke({"query": "incident"})
    assert len(calls) == 1


async def test_delete_and_allowlist_changes_revoke_already_loaded_tools(fake_store, monkeypatch):
    await save(allowed_tools=["search"])
    monkeypatch.setattr(
        runtime,
        "_discover_tools",
        AsyncMock(return_value=[Tool(name="search", inputSchema={"type": "object"})]),
    )
    tool = (await load_mcp_tools(settings.workspace_mcp_source))[0]
    await save(allowed_tools=[])
    assert "allowed" in await tool.ainvoke({})
    await settings.delete_workspace_mcp("example")
    assert "disabled" in await tool.ainvoke({})


async def test_new_connection_exposes_no_tools_until_admin_selects_them(fake_store, monkeypatch):
    await save()
    monkeypatch.setattr(
        runtime, "_discover_tools", AsyncMock(side_effect=AssertionError("must not connect"))
    )
    assert await load_mcp_tools(settings.workspace_mcp_source) == []


async def test_one_failed_server_does_not_hide_other_servers(fake_store, monkeypatch, caplog):
    await save("broken", allowed_tools=["search"])
    await save("working", allowed_tools=["search"])

    async def discover(record, namespace):
        if record.name == "broken":
            raise ValueError("secret upstream detail")
        return [Tool(name="search", inputSchema={"type": "object"})]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    assert [t.name for t in await load_mcp_tools(settings.workspace_mcp_source)] == [
        "mcp_working_search_0ebe441dc6"
    ]
    assert "secret upstream detail" not in caplog.text


async def test_workspace_mcp_catalog_is_reused_until_settings_change(fake_store, monkeypatch):
    await save(allowed_tools=["search1", "search2"])
    calls = 0

    async def discover(record, namespace):
        nonlocal calls
        calls += 1
        return [Tool(name=f"search{calls}", inputSchema={"type": "object"})]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    assert (await load_mcp_tools(settings.workspace_mcp_source))[
        0
    ].name == "mcp_example_search1_882c6b1452"
    assert (await load_mcp_tools(settings.workspace_mcp_source))[
        0
    ].name == "mcp_example_search1_882c6b1452"
    await save(allowed_tools=["search1", "search2"])
    assert (await load_mcp_tools(settings.workspace_mcp_source))[
        0
    ].name == "mcp_example_search2_7f8eb9cd41"


@pytest.mark.parametrize("paginated", [False, True])
async def test_duplicate_catalog_is_isolated_from_other_connections(
    fake_store, monkeypatch, paginated
):
    await settings.save_workspace_mcp(
        "broken",
        MCPConnectionUpdate(
            name="broken", url="https://broken.example/mcp", allowed_tools=["search"]
        ),
    )
    await save("working", allowed_tools=["search"])
    definition = Tool(name="search", inputSchema={"type": "object"})

    class Session:
        def __init__(self, broken):
            self.broken = broken

        async def initialize(self):
            pass

        async def list_tools(self, *, params=None):
            if self.broken and paginated and params is None:
                return ListToolsResult(tools=[definition], nextCursor="next")
            tools = [definition, definition] if self.broken and not paginated else [definition]
            return ListToolsResult(tools=tools)

    @asynccontextmanager
    async def session(connection):
        yield Session(connection["url"] == "https://broken.example/mcp")

    monkeypatch.setattr(runtime, "create_session", session)
    tools = await load_mcp_tools(settings.workspace_mcp_source)
    middleware = DynamicToolMiddleware({"Workspace MCPs": tools})
    assert middleware.has_groups
    assert [tool.name for tool in tools] == ["mcp_working_search_0ebe441dc6"]
    with pytest.raises(ValueError):
        await loader.discover_workspace_mcp("broken")


async def test_expired_catalog_failure_does_not_log_upstream_details(
    fake_store, monkeypatch, caplog
):
    await save(allowed_tools=["search"])
    now = 0
    monkeypatch.setattr(ttl_cache, "_now", lambda: now)
    monkeypatch.setattr(
        runtime,
        "_discover_tools",
        AsyncMock(
            side_effect=[
                [Tool(name="search", inputSchema={"type": "object"})],
                ExceptionGroup("test-secret", [ValueError("test-secret")]),
            ]
        ),
    )
    assert len(await load_mcp_tools(settings.workspace_mcp_source)) == 1
    now = 601
    assert len(await load_mcp_tools(settings.workspace_mcp_source)) == 1
    assert "test-secret" not in caplog.text


@pytest.mark.parametrize("argument", ["config", "run_manager", "self", "runtime"])
async def test_remote_arguments_survive_langchain_invocation(fake_store, monkeypatch, argument):
    await save(allowed_tools=["search"])
    definition = Tool(
        name="search",
        inputSchema={
            "type": "object",
            "properties": {argument: {"type": "string"}},
            "required": [argument],
        },
    )
    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(return_value=[definition]))

    class Session:
        async def initialize(self):
            pass

        async def call_tool(self, name, arguments, **kwargs):
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(arguments))])

    @asynccontextmanager
    async def session(connection, **kwargs):
        yield Session()

    monkeypatch.setattr("langchain_mcp_adapters.tools.create_session", session)
    tool = (await load_mcp_tools(settings.workspace_mcp_source))[0]
    result = await tool.ainvoke(
        {"name": tool.name, "args": {argument: "remote-value"}, "id": "call-1", "type": "tool_call"}
    )
    assert result.tool_call_id == "call-1"
    assert result.status == "success"
    assert json.loads(result.content[0]["text"]) == {argument: "remote-value"}


async def test_personal_mcps_load_only_in_the_owners_private_thread(monkeypatch):
    load = AsyncMock(return_value=["mcp_example_search"])
    monkeypatch.setattr(server, "load_mcp_tools", load)
    threads = {
        "private": {"metadata": {"visibility": "private", "owner_login": "outsider"}},
        "shared": {"metadata": {"visibility": "public", "owner_login": "outsider"}},
    }

    async def get_thread(*, thread_id):
        return threads[thread_id]

    monkeypatch.setattr(server.client.threads, "get", get_thread)

    def namespaces():
        return [source.namespace for source in load.call_args.args]

    assert await server._mcp_tools_for("shared", None) == ["mcp_example_search"]
    assert namespaces() == [("workspace_mcps",)]
    await server._mcp_tools_for("private", "outsider")
    assert namespaces() == [("workspace_mcps",), ("user_mcps", "outsider")]
    await server._mcp_tools_for("private", "OUTSIDER")
    assert namespaces() == [("workspace_mcps",), ("user_mcps", "OUTSIDER")]
    # A collaborative thread, another user's private thread, or an unreadable
    # thread never loads personal credentials.
    for thread_id, login in (
        ("shared", "outsider"),
        ("private", "someone-else"),
        ("missing", "outsider"),
    ):
        await server._mcp_tools_for(thread_id, login)
        assert namespaces() == [("workspace_mcps",)]


async def test_personal_notion_follows_the_same_private_thread_gate(monkeypatch):
    monkeypatch.setattr(server, "_personal_tools_allowed", AsyncMock(return_value=False))
    monkeypatch.setattr(server, "load_notion_tools", AsyncMock(return_value=["notion"]))
    assert await server._notion_tools_for("shared", "notion-owner") == []
    server._personal_tools_allowed.return_value = True
    assert await server._notion_tools_for("private", "notion-owner") == ["notion"]


def test_connection_tool_pairs_cannot_collide():
    pairs = [
        ("foo_bar", "baz"),
        ("foo", "bar_baz"),
        ("example", "a/b"),
        ("example", "a_b"),
        ("example", "a" * 128),
    ]
    names = [runtime._tool_name(*pair) for pair in pairs]
    assert len(set(names)) == len(pairs)
    assert all(len(name) <= 64 for name in names)
