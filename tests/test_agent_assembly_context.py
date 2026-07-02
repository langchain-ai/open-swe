"""Assembly contract for the main agent's context-management + middleware wiring.

Locks in that `get_agent` hands a sandbox `backend` to `create_deep_agent` (which
is what makes deepagents auto-wire `FilesystemMiddleware` tool-result eviction and
`SummarizationMiddleware` history offloading), and that the redundant custom
`RepairOrphanedToolCallsMiddleware` is no longer added explicitly — the built-in
`PatchToolCallsMiddleware` that `create_deep_agent` adds covers it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent.server import get_agent


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> _DummyAgent:
        self.config = config
        return self


def _base_config() -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-ctx",
            "github_login": "octocat",
        },
        "metadata": {},
    }


async def _capture_create_deep_agent_kwargs(
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs: object) -> _DummyAgent:
        captured.update(kwargs)
        return _DummyAgent()

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
            "agent.server.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.server.get_team_default_model_pair",
            new_callable=AsyncMock,
            return_value=(("openai:gpt-5.5", "medium"), ("openai:gpt-5.5", "low")),
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[MagicMock(), MagicMock()]),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config if config is not None else _base_config())

    return captured


@pytest.mark.asyncio
async def test_agent_is_built_with_a_backend_for_eviction_and_summarization() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    # The backend is what enables deepagents' auto-wired FilesystemMiddleware
    # eviction + SummarizationMiddleware offloading.
    assert callable(captured["backend"])


@pytest.mark.asyncio
async def test_agent_does_not_add_custom_repair_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    names = {type(m).__name__ for m in middleware}
    # Built-in PatchToolCallsMiddleware (added by create_deep_agent) replaces it.
    assert "RepairOrphanedToolCallsMiddleware" not in names


@pytest.mark.asyncio
async def test_agent_keeps_message_queue_and_step_limit_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    # The dashboard depends on check_message_queue_before_model; the step-limit
    # notifier must still fire when the lowered run budget is hit.
    present = {type(m).__name__ for m in middleware}
    assert "check_message_queue_before_model" in present
    assert "notify_step_limit_reached" in present


_SLACK_TOOL_NAMES = {
    "slack_add_reaction",
    "slack_read_thread_messages",
    "slack_start_new_thread",
    "slack_thread_reply",
}


def _tool_names(tools: object) -> set[str]:
    assert isinstance(tools, list)
    names: set[str] = set()
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if isinstance(name, str):
            names.add(name)
    return names


@pytest.mark.asyncio
async def test_slack_tools_omitted_without_slack_context() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    names = _tool_names(captured["tools"])
    assert not (_SLACK_TOOL_NAMES & names), (
        "Slack tools must not be registered for dashboard/direct-chat runs "
        "without a slack_thread.channel_id"
    )


@pytest.mark.asyncio
async def test_slack_tools_registered_when_slack_thread_channel_present() -> None:
    config: RunnableConfig = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-slack",
            "github_login": "octocat",
            "source": "slack",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1.0"},
        },
        "metadata": {},
    }
    captured = await _capture_create_deep_agent_kwargs(config)
    names = _tool_names(captured["tools"])
    assert _SLACK_TOOL_NAMES <= names
