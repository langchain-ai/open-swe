from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.runtime import Runtime

from agent import server
from agent.integrations import corridor_mcp


def _FakeTool(name: str) -> StructuredTool:  # noqa: N802
    """A real tool: the search middleware reads descriptions and parameters."""

    async def run(value: str = "") -> str:
        return value

    return StructuredTool.from_function(coroutine=run, name=name, description=f"{name} tool")


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


@pytest.mark.asyncio
async def test_server_load_corridor_mcp_tools() -> None:
    with patch.object(server, "load_corridor_tools", AsyncMock(return_value=["corridor"])):
        assert await server._load_corridor_mcp_tools() == ["corridor"]


@pytest.mark.asyncio
async def test_server_load_corridor_mcp_tools_degrades_on_error() -> None:
    with patch.object(
        server,
        "load_corridor_tools",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        assert await server._load_corridor_mcp_tools() == []


@pytest.mark.asyncio
async def test_get_agent_passes_corridor_prompt_state() -> None:
    config = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-123",
        },
        "metadata": {},
    }

    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)

        class _DummyAgent:
            def with_config(self, _config):
                return self

        return _DummyAgent()

    async def run_with_corridor(configured: bool) -> bool:
        with (
            patch.object(
                server,
                "resolve_github_token",
                new_callable=AsyncMock,
                return_value=("ghp", None),
            ),
            patch.object(server, "resolve_triggering_user_identity", return_value=None),
            patch.object(
                server,
                "ensure_sandbox_for_thread",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch.object(
                server,
                "aresolve_sandbox_work_dir",
                new_callable=AsyncMock,
                return_value="/workspace",
            ),
            patch.object(
                server,
                "get_team_default_model_pair",
                new_callable=AsyncMock,
                return_value=(("openai:gpt-5.6-sol", "medium"), ("openai:gpt-5.6-sol", "low")),
            ),
            patch.object(server, "fallback_model_id_for", return_value=None),
            patch.object(server, "make_model", return_value=MagicMock()),
            patch.object(
                server, "_load_observability_tools", new_callable=AsyncMock, return_value=[]
            ),
            patch.object(
                server,
                "_observability_authorized",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(server, "corridor_configured", return_value=configured),
            patch.object(
                server,
                "_load_corridor_mcp_tools",
                new_callable=AsyncMock,
                return_value=[_FakeTool("analyzePlan")],
            ) as load_corridor,
            patch.object(server, "construct_system_prompt", return_value="prompt") as prompt,
            patch.object(server, "create_deep_agent", side_effect=fake_create_deep_agent),
        ):
            await server.get_agent(cast(RunnableConfig, config))
            middleware = cast(list[object], captured["middleware"])
            prepare = cast(
                AgentMiddleware,
                next(
                    item
                    for item in middleware
                    if type(item).__name__ == "PrepareAgentRunMiddleware"
                ),
            )
            await prepare.abefore_agent(
                cast(AgentState[object], {"messages": []}),
                cast(Runtime[None], MagicMock()),
            )
            # The catalog is a static allowlist, so nothing opens an MCP session
            # before the run's first model call.
            load_corridor.assert_not_awaited()
            if configured:
                names = {
                    getattr(tool, "name", None) for tool in cast(list[object], captured["tools"])
                }
                assert "analyzePlan" not in names
                assert "ToolSearchMiddleware" in {type(item).__name__ for item in middleware}
        return bool(prompt.call_args.kwargs["corridor_enabled"])

    assert await run_with_corridor(False) is False
    assert await run_with_corridor(True) is True
