"""The graph factory tool loaders must overlap, not run back-to-back."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.tools import StructuredTool
from langgraph.graph.state import RunnableConfig

from agent.server import get_agent
from coding_agent.middleware.dynamic_tools import DynamicToolMiddleware
from coding_agent.middleware.plan_mode import PlanModeMiddleware
from coding_agent.sandboxes.state import SANDBOX_BACKENDS

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
@pytest.mark.parametrize("github_login", ["octocat", None])
async def test_workspace_mcps_load_for_non_admins_and_respect_plan_mode(
    initial_plan_mode: bool,
    github_login: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    monkeypatch.setenv("OBSERVABILITY_AUTHORIZED_EMAILS", "other@example.com")
    barrier = asyncio.Barrier(2)

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
        patch("coding_agent.builder.fallback_model_id_for", return_value=None),
        patch("coding_agent.builder.make_model", return_value=MagicMock()),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("coding_agent.builder.create_deep_agent", return_value=_DummyAgent()) as build_agent,
        patch("agent.server.email_for_login", new_callable=AsyncMock, return_value=None),
        patch("agent.server.load_workspace_mcp_tools", side_effect=rendezvous([mcp_tool])),
        patch("agent.server._notion_tools_for", side_effect=rendezvous([])),
    ):
        config = _config()
        config["configurable"]["github_login"] = github_login
        config["configurable"]["plan_mode"] = initial_plan_mode
        await get_agent(config)

    tool_names = {
        tool.name if hasattr(tool, "name") else tool.__name__
        for tool in build_agent.call_args.kwargs["tools"]
    }
    assert "linear_comment" in tool_names
    assert not tool_names.intersection(
        {
            "linear_create_issue",
            "linear_delete_issue",
            "linear_get_issue",
            "linear_get_issue_comments",
            "linear_list_teams",
            "linear_search_issues",
            "linear_update_issue",
        }
    )

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
