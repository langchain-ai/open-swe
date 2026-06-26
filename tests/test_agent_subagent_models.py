from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent.middleware.exclude_tools import ExcludeToolsMiddleware
from agent.server import EXECUTOR_SUBAGENT_PROMPT, get_agent


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> "_DummyAgent":
        self.config = config
        return self


@pytest.mark.asyncio
async def test_agent_uses_profile_executor_model_override() -> None:
    config: RunnableConfig = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-123",
            "github_login": "octocat",
        },
        "metadata": {},
    }
    main_model = MagicMock(name="main_model")
    subagent_model = MagicMock(name="subagent_model")
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
        patch(
            "agent.server.load_profile",
            new_callable=AsyncMock,
            return_value={
                "default_model": "anthropic:claude-opus-4-8",
                "reasoning_effort": "high",
                "default_subagent_model": "openai:gpt-5.5",
                "subagent_reasoning_effort": "xhigh",
            },
        ),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[main_model, subagent_model]) as make_model,
        patch("agent.server.construct_system_prompt", return_value="prompt") as construct_prompt,
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)

    assert captured["model"] is main_model
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    assert subagents[0]["name"] == "executor"
    assert subagents[0]["system_prompt"] == EXECUTOR_SUBAGENT_PROMPT
    assert subagents[0]["model"] is subagent_model
    middleware = captured["middleware"]
    assert any(isinstance(m, ExcludeToolsMiddleware) for m in middleware)
    construct_prompt.assert_called_once()
    assert construct_prompt.call_args.kwargs["planner_executor_mode"] is True
    assert construct_prompt.call_args.kwargs["planner_model_id"] == "anthropic:claude-opus-4-8"
    assert construct_prompt.call_args.kwargs["executor_model_id"] == "openai:gpt-5.5"

    main_call = make_model.call_args_list[0]
    assert main_call.args == ("anthropic:claude-opus-4-8",)
    assert main_call.kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert main_call.kwargs["effort"] == "high"

    subagent_call = make_model.call_args_list[1]
    assert subagent_call.args == ("openai:gpt-5.5",)
    assert subagent_call.kwargs["reasoning"] == {"effort": "xhigh", "summary": "auto"}


@pytest.mark.asyncio
async def test_agent_executor_inherits_profile_model_override_without_explicit_pair() -> None:
    config: RunnableConfig = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-123",
            "github_login": "octocat",
        },
        "metadata": {},
    }
    main_model = MagicMock(name="main_model")
    subagent_model = MagicMock(name="subagent_model")
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
        patch(
            "agent.server.load_profile",
            new_callable=AsyncMock,
            return_value={
                "default_model": "anthropic:claude-opus-4-8",
                "reasoning_effort": "high",
            },
        ),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[main_model, subagent_model]) as make_model,
        patch("agent.server.construct_system_prompt", return_value="prompt") as construct_prompt,
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)

    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    assert subagents[0]["name"] == "executor"
    assert subagents[0]["model"] is subagent_model
    middleware = captured["middleware"]
    assert not any(isinstance(m, ExcludeToolsMiddleware) for m in middleware)
    construct_prompt.assert_called_once()
    assert construct_prompt.call_args.kwargs["planner_executor_mode"] is False
    assert make_model.call_args_list[0].args == ("anthropic:claude-opus-4-8",)
    assert make_model.call_args_list[1].args == ("anthropic:claude-opus-4-8",)
    assert make_model.call_args_list[1].kwargs["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }
    assert make_model.call_args_list[1].kwargs["effort"] == "high"
