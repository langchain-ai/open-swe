"""Tests for Logfire MCP tools integration."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_logfire_tools_returns_empty_when_no_api_key(monkeypatch):
    """Tools gracefully return empty list when LOGFIRE_API_KEY is unset."""
    monkeypatch.setattr("agent.utils.mcp_tools.LOGFIRE_API_KEY", "")

    from agent.utils.mcp_tools import get_logfire_mcp_tools

    tools = await get_logfire_mcp_tools()
    assert tools == []


@pytest.mark.asyncio
async def test_logfire_tools_loaded_when_configured(monkeypatch):
    """Tools are fetched from MCP server when API key is set."""
    monkeypatch.setattr("agent.utils.mcp_tools.LOGFIRE_API_KEY", "test-key-123")
    monkeypatch.setattr(
        "agent.utils.mcp_tools.LOGFIRE_MCP_URL", "https://logfire-us.pydantic.dev/mcp"
    )

    mock_tools = [AsyncMock(name="find_exceptions"), AsyncMock(name="query_traces")]

    with patch("agent.utils.mcp_tools.MultiServerMCPClient") as MockClient:
        instance = MockClient.return_value
        instance.get_tools = AsyncMock(return_value=mock_tools)

        from agent.utils.mcp_tools import get_logfire_mcp_tools

        tools = await get_logfire_mcp_tools()
        assert len(tools) == 2

        MockClient.assert_called_once()
        call_args = MockClient.call_args[0][0]
        assert call_args["logfire"]["url"] == "https://logfire-us.pydantic.dev/mcp"
        assert call_args["logfire"]["headers"]["Authorization"] == "Bearer test-key-123"


@pytest.mark.asyncio
async def test_logfire_tools_graceful_on_connection_failure(monkeypatch):
    """Returns empty list when MCP server is unreachable."""
    monkeypatch.setattr("agent.utils.mcp_tools.LOGFIRE_API_KEY", "test-key-123")

    with patch("agent.utils.mcp_tools.MultiServerMCPClient") as MockClient:
        instance = MockClient.return_value
        instance.get_tools = AsyncMock(side_effect=ConnectionError("unreachable"))

        from agent.utils.mcp_tools import get_logfire_mcp_tools

        tools = await get_logfire_mcp_tools()
        assert tools == []
