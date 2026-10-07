"""CLI MCP reuses the Python tool contract and rechecks caller permissions."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from mcp.types import Tool
from pydantic import ValidationError

from agent.mcp import MCPConnection, runtime
from agent.mcp.cli_tools import CLIArguments, cli_mcp_invoke, cli_mcp_tools
from agent.tools.access import Access


@pytest.mark.asyncio
async def test_catalog_respects_session_admin_and_uses_python_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    assert await cli_mcp_tools({"sub": "user"}) == []
    tools = await cli_mcp_tools({"sub": "admin"})
    by_name = {tool.name: tool for tool in tools}
    assert "manage_feature_flags" in by_name
    assert "read_only_sql" in by_name
    assert "publish_workspace" not in by_name
    assert by_name["manage_feature_flags"].parameters["required"] == ["action"]
    properties = by_name["manage_feature_flags"].parameters["properties"]
    assert isinstance(properties, dict) and "flags" in properties


@pytest.mark.asyncio
async def test_invoke_rechecks_admin_and_runs_private_admin_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    arguments = CLIArguments(root={"action": "read"})
    with pytest.raises(HTTPException) as exc:
        await cli_mcp_invoke("manage_feature_flags", arguments, {"sub": "user"})
    assert exc.value.status_code == 404

    monkeypatch.setattr(
        "agent.tools.access.resolve_access",
        AsyncMock(return_value=Access(admin=True, admin_thread=True, admin_surface=True)),
    )
    get_settings = AsyncMock(return_value={"example": True})
    monkeypatch.setattr("agent.tools.manage_feature_flags.get_instance_settings", get_settings)
    result = await cli_mcp_invoke("manage_feature_flags", arguments, {"sub": "admin"})
    assert isinstance(result.content, dict)
    assert result.content["scope"] == "instance"
    assert "effective" in result.content
    get_settings.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_validates_python_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    with pytest.raises(ValidationError):
        await cli_mcp_invoke(
            "manage_feature_flags", CLIArguments(root={"action": "invalid"}), {"sub": "admin"}
        )


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

    def source(
        scope: runtime.MCPScope, namespace: tuple[str, ...], records: dict[str, MCPConnection]
    ) -> runtime.MCPSource:
        async def list_connections() -> list[MCPConnection]:
            return list(records.values())

        async def get_connection(name: str) -> MCPConnection | None:
            return records.get(name)

        return runtime.MCPSource(namespace, list_connections, get_connection, scope=scope)

    monkeypatch.setattr(
        "agent.mcp.instance.instance_mcp_source",
        lambda: source("instance", ("instance_mcps",), instance),
    )
    monkeypatch.setattr(
        "agent.mcp.workspace.workspace_mcp_source",
        lambda workspace: source("workspace", ("workspace_mcps", workspace), {}),
    )
    monkeypatch.setattr(
        "agent.mcp.user.user_mcp_source",
        lambda login: source("user", ("user_mcps", login), personal if login == "alice" else {}),
    )

    async def discover(record: MCPConnection, namespace: tuple[str, ...]) -> list[Tool]:
        return [Tool(name="search", inputSchema={"type": "object"})]

    @asynccontextmanager
    async def session(connection: dict[str, object], **kwargs: object):
        class RemoteSession:
            async def initialize(self) -> None:
                return None

            async def call_tool(self, name: str, arguments: dict[str, object], **kwargs: object):
                from mcp.types import CallToolResult, TextContent

                return CallToolResult(
                    content=[TextContent(type="text", text=str(connection["url"]))]
                )

        yield RemoteSession()

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    monkeypatch.setattr("langchain_mcp_adapters.tools.create_session", session)
    alice = {"sub": "alice"}
    bob = {"sub": "bob"}
    alice_tools = await cli_mcp_tools(alice)
    remote = next(tool for tool in alice_tools if tool.name.startswith("mcp_linear_search_"))
    assert remote.parameters["type"] == "object"
    assert next(tool for tool in await cli_mcp_tools(bob) if tool.name == remote.name)
    assert "alice.example" in str(
        (await cli_mcp_invoke(remote.name, CLIArguments(root={}), alice)).content
    )
    assert "instance.example" in str(
        (await cli_mcp_invoke(remote.name, CLIArguments(root={}), bob)).content
    )
    personal["linear"] = personal["linear"].model_copy(update={"allowed_tools": []})
    with pytest.raises(HTTPException) as exc:
        await cli_mcp_invoke(remote.name, CLIArguments(root={}), alice)
    assert exc.value.status_code == 404
