from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from mcp.types import CallToolResult, TextContent, Tool

from agent import server
from agent.dashboard import workspace_mcps as settings
from agent.tool_loaders import workspace_mcp as loader


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def save(name="example", **kwargs):
    return await settings.save_workspace_mcp(
        name, settings.WorkspaceMCPUpdate(name=name, url="https://example.com/mcp", **kwargs)
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
    monkeypatch.setattr(loader, "_discover_tools", AsyncMock(return_value=definitions))
    tools = await loader.load_workspace_mcp_tools()
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
        loader,
        "_discover_tools",
        AsyncMock(return_value=[Tool(name="search", inputSchema={"type": "object"})]),
    )
    tool = (await loader.load_workspace_mcp_tools())[0]
    await save(allowed_tools=[])
    assert "allowed" in await tool.ainvoke({})
    await settings.delete_workspace_mcp("example")
    assert "disabled" in await tool.ainvoke({})


async def test_new_connection_exposes_no_tools_until_admin_selects_them(fake_store, monkeypatch):
    await save()
    monkeypatch.setattr(
        loader, "_discover_tools", AsyncMock(side_effect=AssertionError("must not connect"))
    )
    assert await loader.load_workspace_mcp_tools() == []


async def test_one_failed_server_does_not_hide_other_servers(fake_store, monkeypatch, caplog):
    await save("broken", allowed_tools=["search"])
    await save("working", allowed_tools=["search"])

    async def discover(record):
        if record.name == "broken":
            raise ValueError("secret upstream detail")
        return [Tool(name="search", inputSchema={"type": "object"})]

    monkeypatch.setattr(loader, "_discover_tools", discover)
    assert [t.name for t in await loader.load_workspace_mcp_tools()] == [
        "mcp_working_search_0ebe441dc6"
    ]
    assert "secret upstream detail" not in caplog.text


async def test_workspace_mcp_catalog_is_reused_until_settings_change(fake_store, monkeypatch):
    await save(allowed_tools=["search1", "search2"])
    calls = 0

    async def discover(record):
        nonlocal calls
        calls += 1
        return [Tool(name=f"search{calls}", inputSchema={"type": "object"})]

    monkeypatch.setattr(loader, "_discover_tools", discover)
    assert (await loader.load_workspace_mcp_tools())[0].name == "mcp_example_search1_882c6b1452"
    assert (await loader.load_workspace_mcp_tools())[0].name == "mcp_example_search1_882c6b1452"
    await save(allowed_tools=["search1", "search2"])
    assert (await loader.load_workspace_mcp_tools())[0].name == "mcp_example_search2_7f8eb9cd41"


async def test_workspace_tools_use_existing_authorization_gate(monkeypatch):
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.setenv("OBSERVABILITY_AUTHORIZED_EMAILS", "")
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))
    monkeypatch.setattr(
        server, "load_workspace_mcp_tools", AsyncMock(return_value=["mcp_example_search"])
    )
    authorized = {"configurable": {"user_email": "admin@example.com"}}
    outsider = {"configurable": {"user_email": "outsider@example.com"}}
    assert await server._workspace_mcp_tools_for(authorized, None) == ["mcp_example_search"]
    assert await server._workspace_mcp_tools_for(outsider, None) == []


def test_connection_tool_pairs_cannot_collide():
    pairs = [
        ("foo_bar", "baz"),
        ("foo", "bar_baz"),
        ("example", "a/b"),
        ("example", "a_b"),
        ("example", "a" * 128),
    ]
    names = [loader._tool_name(*pair) for pair in pairs]
    assert len(set(names)) == len(pairs)
    assert all(len(name) <= 64 for name in names)
