from typing import Any, Literal, cast
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.tools import StructuredTool

from agent.tool_loaders import notion_mcp


@pytest.fixture(autouse=True)
def _resolve_participant():
    with patch.object(notion_mcp, "resolve_participant", AsyncMock(return_value="alice")):
        yield


@pytest.mark.asyncio
async def test_load_notion_tools_empty_when_not_connected() -> None:
    with patch.object(notion_mcp, "get_notion_access_token", AsyncMock(return_value=None)):
        assert await notion_mcp.load_notion_tools("alice") == []


@pytest.mark.asyncio
async def test_load_notion_tools_degrades_on_error() -> None:
    with (
        patch.object(notion_mcp, "get_notion_access_token", AsyncMock(return_value="tok")),
        patch.object(notion_mcp, "_build_mcp_tools", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        assert await notion_mcp.load_notion_tools("alice") == []


def _notion_tool_for_token(
    token: str,
    response_format: Literal["content", "content_and_artifact"] = "content",
) -> StructuredTool:
    async def notion_search(query: str):
        """Search Notion."""
        content = {"query": query, "token": token}
        if response_format == "content_and_artifact":
            return content, {"artifact_token": token}
        return content

    return StructuredTool.from_function(
        coroutine=notion_search,
        name="notion_search",
        description="Search Notion",
        response_format=response_format,
    )


@pytest.mark.asyncio
async def test_load_notion_tools_returns_wrappers() -> None:
    discovered = _notion_tool_for_token("initial-token")
    with (
        patch.object(notion_mcp, "get_notion_access_token", AsyncMock(return_value="tok")),
        patch.object(notion_mcp, "_build_mcp_tools", AsyncMock(return_value=[discovered])),
    ):
        tools = await notion_mcp.load_notion_tools("alice")
    assert len(tools) == 1
    assert tools[0].name == "notion_search"
    assert tools[0].description == discovered.description
    schema = cast("dict[str, Any]", tools[0].args_schema)
    assert "query" in schema["properties"]
    assert "on_behalf_of" in schema["properties"]
    assert "on_behalf_of" in schema["required"]
    assert tools[0].response_format == "content"


@pytest.mark.asyncio
async def test_notion_wrapper_normalizes_content_and_artifact_tools() -> None:
    get_token = AsyncMock(side_effect=["initial-token", "fresh-token"])
    build_tools = AsyncMock(
        side_effect=lambda token: [_notion_tool_for_token(token, "content_and_artifact")]
    )
    with (
        patch.object(notion_mcp, "get_notion_access_token", get_token),
        patch.object(notion_mcp, "_build_mcp_tools", build_tools),
    ):
        tools = await notion_mcp.load_notion_tools("alice")
        assert tools[0].response_format == "content"
        result = await tools[0].ainvoke({"on_behalf_of": "alice", "query": "roadmap"})
    assert result == {"query": "roadmap", "token": "fresh-token"}


@pytest.mark.asyncio
async def test_notion_wrapper_refreshes_token_at_call_time() -> None:
    get_token = AsyncMock(side_effect=["initial-token", "fresh-token"])
    build_tools = AsyncMock(side_effect=lambda token: [_notion_tool_for_token(token)])
    with (
        patch.object(notion_mcp, "get_notion_access_token", get_token),
        patch.object(notion_mcp, "_build_mcp_tools", build_tools),
    ):
        tools = await notion_mcp.load_notion_tools("alice")
        result = await tools[0].ainvoke({"on_behalf_of": "alice", "query": "roadmap"})
    assert result == {"query": "roadmap", "token": "fresh-token"}
    assert get_token.await_count == 2
    assert [call.args[0] for call in build_tools.await_args_list] == [
        "initial-token",
        "fresh-token",
    ]


@pytest.mark.asyncio
async def test_notion_wrapper_fails_when_token_missing_at_call_time() -> None:
    get_token = AsyncMock(side_effect=["initial-token", None])
    build_tools = AsyncMock(return_value=[_notion_tool_for_token("initial-token")])
    with (
        patch.object(notion_mcp, "get_notion_access_token", get_token),
        patch.object(notion_mcp, "_build_mcp_tools", build_tools),
    ):
        tools = await notion_mcp.load_notion_tools("alice")
        with pytest.raises(RuntimeError, match="Notion MCP authorization unavailable"):
            await tools[0].ainvoke({"on_behalf_of": "alice", "query": "roadmap"})
    assert build_tools.await_count == 1
