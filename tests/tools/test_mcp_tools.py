import json
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from openswe.mcp import runtime
from openswe.middleware.dynamic_tools import DynamicToolMiddleware
from openswe.utils import ttl_cache
from tests.mcp_helpers import MemorySource, fake_mcp_server


@pytest.fixture
def servers() -> MemorySource:
    return MemorySource()


async def load(servers: MemorySource) -> runtime.MCPTools:
    return await runtime.load_mcp_tools(servers.source)


async def test_generic_tools_are_namespaced_filtered_and_refresh_credentials(servers, monkeypatch):
    servers.save(headers={"Authorization": "old"}, allowed_tools=["search"])
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
    tools = await load(servers)
    assert [tool.name for tool in tools] == ["mcp_example_search"]
    calls = []

    async def call(transport, name, arguments):
        assert transport.headers == {"X-Api-Key": "rotated"}
        assert transport.httpx_client_factory is not None
        calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text="found")])

    fake_mcp_server(monkeypatch, call=call)
    servers.save(headers={"X-Api-Key": "rotated"}, allowed_tools=["search"])
    result = await tools[0].ainvoke({"query": "incident", "runtime": "python"})
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "found"
    assert calls == [("search", {"query": "incident", "runtime": "python"})]
    servers.save(enabled=False)
    assert "disabled" in await tools[0].ainvoke({"query": "incident"})
    assert len(calls) == 1


async def test_delete_and_allowlist_changes_revoke_already_loaded_tools(servers, monkeypatch):
    servers.save(allowed_tools=["search"])
    monkeypatch.setattr(
        runtime,
        "_discover_tools",
        AsyncMock(return_value=[Tool(name="search", input_schema={"type": "object"})]),
    )
    tool = (await load(servers))[0]
    servers.save(allowed_tools=[])
    assert "allowed" in await tool.ainvoke({})
    del servers.records["example"]
    assert "disabled" in await tool.ainvoke({})


async def test_a_server_naming_no_tools_exposes_none(servers, monkeypatch):
    servers.save()
    monkeypatch.setattr(
        runtime, "_discover_tools", AsyncMock(side_effect=AssertionError("must not connect"))
    )
    assert await load(servers) == []


async def test_duplicate_catalog_is_isolated_from_other_connections(servers, monkeypatch):
    broken = servers.save("broken", "https://broken.example/mcp", allowed_tools=["search"])
    servers.save("working", allowed_tools=["search"])
    definition = Tool(name="search", input_schema={"type": "object"})

    async def list_tools(transport):
        broken = transport.url == "https://broken.example/mcp"
        return [definition, definition] if broken else [definition]

    fake_mcp_server(monkeypatch, tools=list_tools)
    tools = await load(servers)
    middleware = DynamicToolMiddleware({"MCPs": tools})
    assert middleware.has_groups
    assert [tool.name for tool in tools] == ["mcp_working_search"]
    with pytest.raises(ValueError):
        await runtime.discover_tools(broken, servers.source.namespace)


async def test_expired_catalog_failure_does_not_log_upstream_details(servers, monkeypatch, caplog):
    servers.save(allowed_tools=["search"])
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
    assert len(await load(servers)) == 1
    now = 601
    assert len(await load(servers)) == 1
    assert "test-secret" not in caplog.text


@pytest.mark.parametrize("argument", ["config", "run_manager", "self", "runtime"])
async def test_remote_arguments_survive_langchain_invocation(servers, monkeypatch, argument):
    servers.save(allowed_tools=["search"])
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
    tool = (await load(servers))[0]
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
        ("example", runtime._tool_name("example", "a/b").removeprefix("mcp_example_")),
        ("example", "search"),
    ]
    names = [runtime._tool_name(*pair) for pair in pairs]
    assert len(set(names)) == len(pairs)
    assert all(len(name) <= 64 for name in names)
    assert names[-1] == "mcp_example_search"
