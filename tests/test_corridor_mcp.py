from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.integrations import corridor_mcp


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture(autouse=True)
def clear_corridor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CORRIDOR_API_TOKEN",
        "CORRIDOR_MCP_TOKEN",
        "CORRIDOR_TOKEN",
        "CORRIDOR_MCP_URL",
        "CORRIDOR_MCP_SERVER_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_load_corridor_mcp_config_empty_without_token() -> None:
    assert corridor_mcp.load_corridor_mcp_config() is None


def test_load_corridor_mcp_config_uses_default_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORRIDOR_API_TOKEN", "tok")

    config = corridor_mcp.load_corridor_mcp_config()

    assert config == corridor_mcp.CorridorMCPConfig(
        url=corridor_mcp.DEFAULT_CORRIDOR_MCP_URL,
        token="tok",
    )


def test_load_corridor_mcp_config_accepts_query_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORRIDOR_MCP_URL", "https://app.corridor.dev/api/mcp?token=tok")

    config = corridor_mcp.load_corridor_mcp_config()

    assert config == corridor_mcp.CorridorMCPConfig(
        url=corridor_mcp.DEFAULT_CORRIDOR_MCP_URL,
        token="tok",
    )


def test_load_corridor_mcp_config_rejects_non_corridor_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORRIDOR_API_TOKEN", "tok")
    monkeypatch.setenv("CORRIDOR_MCP_URL", "https://example.com/api/mcp")

    assert corridor_mcp.load_corridor_mcp_config() is None


@pytest.mark.asyncio
async def test_load_corridor_tools_empty_when_not_configured() -> None:
    assert await corridor_mcp.load_corridor_tools() == []


@pytest.mark.asyncio
async def test_load_corridor_tools_degrades_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORRIDOR_API_TOKEN", "tok")

    with patch.object(
        corridor_mcp,
        "_build_mcp_tools",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        assert await corridor_mcp.load_corridor_tools() == []


@pytest.mark.asyncio
async def test_load_corridor_tools_returns_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORRIDOR_API_TOKEN", "tok")
    analyze_plan = _FakeTool("analyzePlan")
    other_tool = _FakeTool("otherTool")

    with patch.object(
        corridor_mcp,
        "_build_mcp_tools",
        AsyncMock(return_value=[other_tool, analyze_plan]),
    ):
        assert await corridor_mcp.load_corridor_tools() == [analyze_plan]
