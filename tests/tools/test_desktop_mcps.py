import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.dashboard import desktop_mcps
from agent.mcp.runtime import MCPTool
from agent.tool_loaders import desktop_mcp


async def test_desktop_local_override_and_revocation(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"example": {"enabled": False}}}))
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
