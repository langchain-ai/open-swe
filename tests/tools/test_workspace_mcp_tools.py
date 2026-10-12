import json
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from mcp.types import CallToolResult, TextContent, Tool

from openswe.mcp import MCPConnectionUpdate, runtime
from openswe.mcp import workspace as settings
from openswe.middleware.dynamic_tools import DynamicToolMiddleware
from openswe.tool_loaders import workspace_mcp as loader
from openswe.utils import ttl_cache
from tests.mcp_helpers import fake_mcp_server


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def save(name="example", **kwargs):
    return await settings.save_workspace_mcp(
        "default", name, MCPConnectionUpdate(name=name, url="https://example.com/mcp", **kwargs)
    )


async def test_generic_tools_are_namespaced_filtered_and_refresh_credentials(
    fake_store, monkeypatch
):
    await save(headers={"Authorization": "old"}, allowed_tools=["search"])
    definitions = [
        Tool(
            name=name,
            description=name,
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "runtime": {"type": "string"}},
                "required": ["query"],
            },
        )
        for name in ["search", "delete"]
    ]
    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(return_value=definitions))
    tools = await loader.load_workspace_mcp_tools("default")
    assert [tool.name for tool in tools] == ["mcp_example_search_8245e54055"]
    calls = []

    async def call(transport, name, arguments):
        assert transport.headers == {"X-Api-Key": "rotated"}
        assert transport.httpx_client_factory is not None
        calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text="found")])

    fake_mcp_server(monkeypatch, call=call)
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
        AsyncMock(return_value=[Tool(name="search", input_schema={"type": "object"})]),
    )
    tool = (await loader.load_workspace_mcp_tools("default"))[0]
    await save(allowed_tools=[])
    assert "allowed" in await tool.ainvoke({})
    await settings.delete_workspace_mcp("default", "example")
    assert "disabled" in await tool.ainvoke({})


async def test_new_connection_exposes_no_tools_until_admin_selects_them(fake_store, monkeypatch):
    await save()
    monkeypatch.setattr(
        runtime, "_discover_tools", AsyncMock(side_effect=AssertionError("must not connect"))
    )
    assert await loader.load_workspace_mcp_tools("default") == []


async def test_duplicate_catalog_is_isolated_from_other_connections(fake_store, monkeypatch):
    await settings.save_workspace_mcp(
        "default",
        "broken",
        MCPConnectionUpdate(
            name="broken", url="https://broken.example/mcp", allowed_tools=["search"]
        ),
    )
    await save("working", allowed_tools=["search"])
    definition = Tool(name="search", input_schema={"type": "object"})

    async def list_tools(transport):
        broken = transport.url == "https://broken.example/mcp"
        return [definition, definition] if broken else [definition]

    fake_mcp_server(monkeypatch, tools=list_tools)
    tools = await loader.load_workspace_mcp_tools("default")
    middleware = DynamicToolMiddleware({"Workspace MCPs": tools})
    assert middleware.has_groups
    assert [tool.name for tool in tools] == ["mcp_working_search_0ebe441dc6"]
    with pytest.raises(ValueError):
        await loader.discover_workspace_mcp("default", "broken")


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
                [Tool(name="search", input_schema={"type": "object"})],
                ExceptionGroup("test-secret", [ValueError("test-secret")]),
            ]
        ),
    )
    assert len(await loader.load_workspace_mcp_tools("default")) == 1
    now = 601
    assert len(await loader.load_workspace_mcp_tools("default")) == 1
    assert "test-secret" not in caplog.text


@pytest.mark.parametrize("argument", ["config", "run_manager", "self", "runtime"])
async def test_remote_arguments_survive_langchain_invocation(fake_store, monkeypatch, argument):
    await save(allowed_tools=["search"])
    definition = Tool(
        name="search",
        input_schema={
            "type": "object",
            "properties": {argument: {"type": "string"}},
            "required": [argument],
        },
    )
    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(return_value=[definition]))

    async def call(transport, name, arguments):
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(arguments))])

    fake_mcp_server(monkeypatch, call=call)
    tool = (await loader.load_workspace_mcp_tools("default"))[0]
    result = await tool.ainvoke(
        {"name": tool.name, "args": {argument: "remote-value"}, "id": "call-1", "type": "tool_call"}
    )
    assert result.tool_call_id == "call-1"
    assert result.status == "success"
    assert json.loads(result.content[0]["text"]) == {argument: "remote-value"}


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
