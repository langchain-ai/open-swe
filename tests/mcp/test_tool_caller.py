"""The remote MCP reuses the Python tool contract and rechecks caller permissions."""

import json
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import JsonValue

from openswe.mcp import MCPConnection, runtime
from openswe.mcp.caller import ToolCaller, UnknownTool
from openswe.sandboxes.tool_runtime import tool_parameters
from tests.mcp_helpers import Transport, fake_mcp_server

USER = ToolCaller(login="user", email=None)
ADMIN = ToolCaller(login="admin", email=None)


@pytest.mark.asyncio
async def test_catalog_respects_admin_and_uses_python_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    assert {access for _, access in (await USER.tools()).values()} == {"session"}
    tools = await ADMIN.tools()
    assert "read_only_sql" in tools
    assert "upload_session" in tools
    assert "publish_workspace" not in tools
    parameters = tool_parameters(tools["manage_feature_flags"][0])
    assert parameters["required"] == ["action"]
    properties = parameters["properties"]
    assert isinstance(properties, dict) and "flags" in properties


@pytest.mark.asyncio
async def test_invoke_rechecks_admin_and_runs_private_admin_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    with pytest.raises(UnknownTool):
        await USER.invoke("manage_feature_flags", {"action": "read"})

    get_settings = AsyncMock(return_value={"example": True})
    monkeypatch.setattr("openswe.tools.manage_feature_flags.get_instance_settings", get_settings)
    result = await ADMIN.invoke("manage_feature_flags", {"action": "read"})
    assert isinstance(result.content, str)
    content = json.loads(result.content)
    assert content["scope"] == "instance"
    assert "effective" in content
    get_settings.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_validates_python_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    result = await ADMIN.invoke("manage_feature_flags", {"action": "invalid"})
    assert result.status == "error"
    assert "action" in str(result.content)


@pytest.mark.asyncio
async def test_remote_mcp_tools_use_scoped_sources_and_recheck_allowed_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = {
        "linear": MCPConnection(
            name="linear",
            url="https://instance.example/mcp",
            allowed_tools=["search"],
            revision="instance-revision",
            updated_at="2026-09-09T00:00:00Z",
        )
    }
    personal = {
        "linear": MCPConnection(
            name="linear",
            url="https://alice.example/mcp",
            allowed_tools=["search"],
            revision="user-revision",
            updated_at="2026-09-09T00:00:00Z",
        )
    }

    def source(namespace: tuple[str, ...], records: dict[str, MCPConnection]) -> runtime.MCPSource:
        async def list_connections() -> list[MCPConnection]:
            return list(records.values())

        async def get_connection(name: str) -> MCPConnection | None:
            return records.get(name)

        return runtime.MCPSource(namespace, list_connections, get_connection)

    monkeypatch.setattr(
        "openswe.mcp.instance.instance_mcp_source",
        lambda: source(("instance_mcps",), instance),
    )
    monkeypatch.setattr(
        "openswe.mcp.workspace.workspace_mcp_source",
        lambda workspace: source(("workspace_mcps", workspace), {}),
    )
    monkeypatch.setattr(
        "openswe.mcp.user.user_mcp_source",
        lambda login: source(("user_mcps", login), personal if login == "alice" else {}),
    )

    async def discover(record: MCPConnection, namespace: tuple[str, ...]) -> list[Tool]:
        return [Tool(name="search", input_schema={"type": "object"})]

    async def call(
        transport: Transport, name: str, arguments: dict[str, JsonValue]
    ) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text=transport.url)])

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    fake_mcp_server(monkeypatch, call=call)
    alice = ToolCaller(login="alice", email=None)
    bob = ToolCaller(login="bob", email=None)
    name = next(name for name in await alice.tools() if name.startswith("mcp_linear_search_"))
    assert tool_parameters((await alice.tools())[name][0])["type"] == "object"
    assert name in await bob.tools()
    assert "alice.example" in str((await alice.invoke(name, {})).content)
    assert "instance.example" in str((await bob.invoke(name, {})).content)
    personal["linear"] = personal["linear"].model_copy(update={"allowed_tools": []})
    with pytest.raises(UnknownTool):
        await alice.invoke(name, {})
