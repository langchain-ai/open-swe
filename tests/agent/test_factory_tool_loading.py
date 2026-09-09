"""The graph factory's remaining tool loaders must overlap, not run back-to-back.

Each is a cold-cache network round trip (an MCP handshake, credential store
reads) sitting on the critical path before the run's first model call. Corridor
no longer appears here: its catalog is static, so it loads on demand instead.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.tools import StructuredTool
from langgraph.graph.state import RunnableConfig

from agent.middleware.dynamic_tools import DynamicToolMiddleware
from agent.middleware.plan_mode import PlanModeMiddleware
from agent.sandboxes.state import SANDBOX_BACKENDS
from agent.server import get_agent

_START_TIMEOUT_SECONDS = 2.0


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> _DummyAgent:
        return self


def _config() -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-parallel-tools",
            "github_login": "octocat",
        },
        "metadata": {},
    }


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_store")
@pytest.mark.parametrize("initial_plan_mode", [False, True])
async def test_tool_loaders_run_concurrently_and_gate_workspace_mcps(
    initial_plan_mode: bool,
) -> None:
    barrier = asyncio.Barrier(3)

    async def delete_incident() -> str:
        return "deleted"

    mcp_tool = StructuredTool.from_function(
        coroutine=delete_incident,
        name="mcp_incident_delete_0123456789",
        description="Delete an incident",
    )

    def rendezvous(result: Any) -> Any:
        # Serial loaders never all reach the barrier, so a regression times out
        # here instead of quietly costing a few seconds per run.
        async def loader(*_args: Any) -> Any:
            await asyncio.wait_for(barrier.wait(), timeout=_START_TIMEOUT_SECONDS)
            return result

        return loader

    thread_id = "thread-parallel-tools"
    SANDBOX_BACKENDS.pop(thread_id, None)
    with (
        patch(
            "agent.server.resolve_github_token",
            new_callable=AsyncMock,
            return_value=("ghp", None),
        ),
        patch("agent.server.resolve_triggering_user_identity", return_value=None),
        patch(
            "agent.server.ensure_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "agent.server.resolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.server.get_team_default_model_pair",
            new_callable=AsyncMock,
            return_value=(("openai:gpt-5.6-sol", "medium"), ("openai:gpt-5.6-sol", "low")),
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.server.load_thread_settings", new_callable=AsyncMock, return_value={}),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", return_value=MagicMock()),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()) as build_agent,
        patch("agent.server._observability_tools_for", side_effect=rendezvous([])),
        patch("agent.server._workspace_mcp_tools_for", side_effect=rendezvous([mcp_tool])),
        patch("agent.server._load_integration_tools", side_effect=rendezvous(([], []))),
    ):
        config = _config()
        config["configurable"]["plan_mode"] = initial_plan_mode
        await get_agent(config)

    middleware = build_agent.call_args.kwargs["middleware"]
    dynamic = next(item for item in middleware if isinstance(item, DynamicToolMiddleware))
    plan_mode = next(item for item in middleware if isinstance(item, PlanModeMiddleware))
    assert middleware.index(dynamic) < middleware.index(plan_mode)
    captured: list[str] = []

    async def capture(request: ModelRequest) -> Any:
        captured.extend(tool.name for tool in request.tools)
        return MagicMock()

    async def apply_plan_mode(request: ModelRequest) -> Any:
        return await plan_mode.awrap_model_call(request, capture)

    for state, expected in [
        ({}, [] if initial_plan_mode else [mcp_tool.name]),
        ({"plan_mode": True}, []),
        ({"plan_mode": False}, [mcp_tool.name]),
    ]:
        captured.clear()
        request = ModelRequest(
            model=MagicMock(),
            messages=[],
            tools=[],
            runtime=MagicMock(),
            state={**state, "messages": [], "loaded_integration_tools": [mcp_tool.name]},
        )
        await dynamic.awrap_model_call(request, apply_plan_mode)
        assert captured == expected

    SANDBOX_BACKENDS.pop(thread_id, None)
