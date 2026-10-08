"""Integration tools the graph factory loads into a run."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import langgraph_sdk
import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.tools import StructuredTool
from langgraph.graph.state import RunnableConfig

from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.middleware.dynamic_tools import DynamicToolMiddleware
from openswe.sandboxes.state import SANDBOX_BACKENDS
from openswe.server import get_agent

_MODEL_DEFAULTS = {
    "default_agent_model": "openai:gpt-5.6-sol",
    "default_agent_reasoning_effort": "medium",
    "default_agent_subagent_model": "openai:gpt-5.6-sol",
    "default_agent_subagent_reasoning_effort": "low",
}


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> _DummyAgent:
        return self


def _config() -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-factory-tools",
            "github_login": "octocat",
        },
        "metadata": {},
    }


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_store")
@pytest.mark.parametrize("initial_plan_mode", [False, True])
@pytest.mark.parametrize("github_login", ["octocat", None])
async def test_workspace_mcps_load_for_non_admins_with_legacy_plan_state(
    initial_plan_mode: bool,
    github_login: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(
                get=AsyncMock(return_value={"metadata": {"visibility": "public"}})
            )
        ),
    )

    async def delete_incident() -> str:
        return "deleted"

    mcp_tool = StructuredTool.from_function(
        coroutine=delete_incident,
        name="mcp_incident_delete_0123456789",
        description="Delete an incident",
    )

    thread_id = "thread-factory-tools"
    SANDBOX_BACKENDS.pop(thread_id, None)
    with (
        patch(
            "openswe.server.resolve_github_token",
            new_callable=AsyncMock,
            return_value=("ghp", None),
        ),
        patch("openswe.server.resolve_triggering_user_identity", return_value=None),
        patch(
            "openswe.server.ensure_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "openswe.server.resolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "openswe.server.cached_workspace_settings",
            new_callable=AsyncMock,
            return_value=WorkspaceSettings(_MODEL_DEFAULTS),
        ),
        patch("openswe.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch("openswe.server.load_thread_settings", new_callable=AsyncMock, return_value={}),
        patch("openswe.server.fallback_model_id_for", return_value=None),
        patch("openswe.server.make_model", return_value=MagicMock()),
        patch("openswe.server.construct_system_prompt", return_value="prompt"),
        patch("openswe.server.create_deep_agent", return_value=_DummyAgent()) as build_agent,
        patch("openswe.users.User.email_for_login", new_callable=AsyncMock, return_value=None),
        patch("openswe.server._mcp_tools_for", new_callable=AsyncMock, return_value=[mcp_tool]),
    ):
        config = _config()
        config["configurable"]["github_login"] = github_login
        config["configurable"]["plan_mode"] = initial_plan_mode
        await get_agent(config)

    tool_names = {
        tool.name if hasattr(tool, "name") else tool.__name__
        for tool in build_agent.call_args.kwargs["tools"]
    }
    assert not tool_names.intersection(
        {
            "linear_comment",
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
    captured: list[str] = []

    async def capture(request: ModelRequest) -> Any:
        captured.extend(tool.name for tool in request.tools)
        return MagicMock()

    for state, expected in [
        ({}, [mcp_tool.name]),
        ({"plan_mode": True}, [mcp_tool.name]),
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
        await dynamic.awrap_model_call(request, capture)
        assert captured == expected

    SANDBOX_BACKENDS.pop(thread_id, None)
