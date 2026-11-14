"""The remote MCP reuses the Python tool contract and rechecks caller permissions."""

import json
from unittest.mock import AsyncMock

import pytest

from openswe.mcp.caller import ToolCaller, UnknownTool
from openswe.sandboxes.tool_runtime import tool_parameters

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
    assert "execute_database_sql" in tools
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
