"""CLI MCP reuses the Python tool contract and rechecks caller permissions."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from agent.mcp.cli_tools import CLIArguments, cli_mcp_invoke, cli_mcp_tools


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
    assert "flags" in by_name["manage_feature_flags"].parameters["properties"]


@pytest.mark.asyncio
async def test_invoke_rechecks_admin_and_runs_private_admin_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")
    arguments = CLIArguments(root={"action": "read"})
    with pytest.raises(HTTPException) as exc:
        await cli_mcp_invoke("manage_feature_flags", arguments, {"sub": "user"})
    assert exc.value.status_code == 404

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
