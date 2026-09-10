"""The compiled middleware sequence is a behavioural contract.

Middleware nest in list order, so the sequence decides which guard sees a model
call or tool result first. These assertions pin the exact sequence for the main
agent's representative configurations.
"""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent.server import get_agent
from coding_agent.sandboxes.state import SANDBOX_BACKENDS


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> _DummyAgent:
        del config
        return self


def _config(**configurable: Any) -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-middleware-order",
            "github_login": "octocat",
            **configurable,
        },
        "metadata": {},
    }


async def _middleware_names(
    config: RunnableConfig,
    *,
    profile: dict[str, object] | None = None,
    fallback_model_id: str | None = None,
    browser_tools: list[Any] | None = None,
) -> list[str]:
    captured: dict[str, Any] = {}

    def fake_create_deep_agent(**kwargs: Any) -> _DummyAgent:
        captured.update(kwargs)
        return _DummyAgent()

    thread_id = str((config.get("configurable") or {}).get("thread_id"))
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
        patch(
            "agent.server.get_team_agent_routing_models",
            new_callable=AsyncMock,
            return_value={
                "fast": ("google_genai:gemini-3.8-flash", "low"),
                "balanced": ("openai:gpt-5.6-sol", "medium"),
                "performance": ("anthropic:claude-opus-5", "high"),
            },
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=profile),
        patch("agent.server.load_thread_settings", new_callable=AsyncMock, return_value={}),
        patch("agent.server.load_workspace_mcp_tools", new_callable=AsyncMock, return_value=[]),
        patch("agent.server.load_browser_tools", return_value=browser_tools or []),
        patch("agent.server._notion_tools_for", new_callable=AsyncMock, return_value=[]),
        patch("coding_agent.builder.fallback_model_id_for", return_value=fallback_model_id),
        patch("coding_agent.utils.model.make_model", return_value=MagicMock()),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("coding_agent.builder.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)
    SANDBOX_BACKENDS.pop(thread_id, None)

    return [type(item).__name__ for item in cast(list[Any], captured["middleware"])]


@pytest.mark.asyncio
async def test_default_middleware_order() -> None:
    assert await _middleware_names(_config()) == [
        "PrepareAgentRunMiddleware",
        "SanitizeToolInputsMiddleware",
        "ModelCallLimitMiddleware",
        "ToolErrorMiddleware",
        "ExcludeToolsMiddleware",
        "SubdirAgentsReadMiddleware",
        "ToolRetryMiddleware",
        "PullRequestCreationGuardMiddleware",
        "WorkflowPushGuardMiddleware",
        "refresh_github_proxy_before_model",
        "check_message_queue_before_model",
        "TimeoutWrapupMiddleware",
        "notify_step_limit_reached",
        "RecordRunUsageMiddleware",
        "PlanModeMiddleware",
        "SanitizeFireworksMessagesMiddleware",
        "SanitizeOpenAIResponsesMiddleware",
        "SanitizeThinkingBlocksMiddleware",
        "StableToolResultOrderMiddleware",
        "ModelErrorMiddleware",
        "ModelCallTimeoutMiddleware",
    ]


@pytest.mark.asyncio
async def test_routing_and_fallback_middleware_sit_between_run_usage_and_plan_mode() -> None:
    names = await _middleware_names(
        _config(),
        profile={"model_routing_enabled": True},
        fallback_model_id="anthropic:claude-sonnet-4.5",
    )
    assert names[
        names.index("RecordRunUsageMiddleware") + 1 : names.index("PlanModeMiddleware")
    ] == [
        "ModelSelectionMiddleware",
        "ModelFallbackMiddleware",
    ]


@pytest.mark.asyncio
async def test_dynamic_tools_middleware_sits_directly_after_prepare() -> None:
    from langchain_core.tools import StructuredTool

    async def sample_integration_tool() -> str:
        """Sample."""
        return "ok"

    tool = StructuredTool.from_function(coroutine=sample_integration_tool)
    names = await _middleware_names(_config(), browser_tools=[tool])
    assert names[:3] == [
        "PrepareAgentRunMiddleware",
        "DynamicToolMiddleware",
        "SanitizeToolInputsMiddleware",
    ]


@pytest.mark.asyncio
async def test_stop_summary_drops_message_queue_hook() -> None:
    names = await _middleware_names(
        _config(
            source="slack",
            slack_thread={"channel_id": "C123", "thread_ts": "1700000000.000100"},
            stop_summary=True,
        )
    )
    assert "check_message_queue_before_model" not in names
    assert names[names.index("WorkflowPushGuardMiddleware") + 1 :][:3] == [
        "refresh_github_proxy_before_model",
        "TimeoutWrapupMiddleware",
        "notify_step_limit_reached",
    ]


@pytest.mark.asyncio
async def test_desktop_run_drops_pull_request_guard() -> None:
    with patch("agent.server.create_desktop_backend", return_value=MagicMock()):
        names = await _middleware_names(_config(source="desktop", local_project_path="/tmp"))
    assert "PullRequestCreationGuardMiddleware" not in names
    assert names[names.index("ToolRetryMiddleware") + 1] == "WorkflowPushGuardMiddleware"
