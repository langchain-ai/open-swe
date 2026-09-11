import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from langchain_mcp_adapters.sessions import Connection
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import CallToolResult, TextContent, Tool

from agent.dashboard import desktop_mcps
from agent.mcp.runtime import MCPTool
from agent.tool_loaders import desktop_mcp


async def test_local_mcp_forwards_reserved_runtime(monkeypatch):
    call = AsyncMock(return_value=CallToolResult(content=[TextContent(type="text", text="ok")]))

    @asynccontextmanager
    async def session(*args, **kwargs):
        yield SimpleNamespace(initialize=AsyncMock(), call_tool=call)

    monkeypatch.setattr("langchain_mcp_adapters.tools.create_session", session)
    monkeypatch.setattr(desktop_mcp, "_local_servers", lambda: {"local": {}})
    connection: Connection = {"transport": "stdio", "command": "unused", "args": []}
    definition = Tool(
        name="search", inputSchema={"type": "object", "properties": {"runtime": {"type": "string"}}}
    )
    adapter = convert_mcp_tool_to_langchain_tool(None, definition, connection=connection)
    tool = desktop_mcp._local_tool("local", {}, connection, adapter)
    assert (await tool.ainvoke({"runtime": "python"}))[0]["text"] == "ok"
    assert call.call_args.args[1] == {"runtime": "python"}


async def test_desktop_local_override_and_revocation(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"example": {"enabled": False}}}))
    path.chmod(0o600)
    monkeypatch.setenv("OPEN_SWE_LOCAL_MCPS_FILE", str(path))
    definition = {
        "name": "remote",
        "description": "remote",
        "schema": {"type": "object"},
        "metadata": {"mcp_connection": "example"},
    }
    request = AsyncMock(
        return_value={"login": "alice", "backend": "https://cloud.example", "tools": [definition]}
    )
    monkeypatch.setattr(desktop_mcp, "_cloud_request", request)
    assert await desktop_mcp.load_desktop_mcp_tools() == []
    path.write_text('{"mcpServers": {}}')
    tools = await desktop_mcp.load_desktop_mcp_tools()
    path.write_text(json.dumps({"mcpServers": {"example": {"enabled": False}}}))
    assert "changed" in await tools[0].ainvoke({})
    assert request.await_count == 2


async def test_malformed_local_mcp_is_ignored(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"broken": None}}))
    path.chmod(0o600)
    monkeypatch.setenv("OPEN_SWE_LOCAL_MCPS_FILE", str(path))
    monkeypatch.setattr(desktop_mcp, "_cloud_request", AsyncMock(side_effect=RuntimeError))
    assert await desktop_mcp.load_desktop_mcp_tools() == []


async def test_cloud_desktop_call_rejects_account_and_scope_changes(monkeypatch):
    invoke = AsyncMock(return_value="result")
    tool = MCPTool.from_function(
        coroutine=invoke,
        name="search",
        description="Search",
        args_schema={"type": "object"},
        metadata={"scope": "alice"},
    )
    monkeypatch.setattr(desktop_mcps, "desktop_mcp_tools", AsyncMock(return_value=[tool]))
    request = {"login": "alice", "name": "search", "metadata": {"scope": "alice"}}
    assert await desktop_mcps.call_desktop_mcp("alice", request) == "result"
    with pytest.raises(HTTPException):
        await desktop_mcps.call_desktop_mcp("bob", request)
    request["metadata"] = {"scope": "workspace"}
    with pytest.raises(HTTPException):
        await desktop_mcps.call_desktop_mcp("alice", request)
    assert invoke.await_count == 1
