from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent.server import get_agent


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> "_DummyAgent":
        self.config = config
        return self


@pytest.mark.asyncio
async def test_agent_uses_profile_subagent_model_override() -> None:
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
            return_value=(("openai:gpt-5.6-sol", "medium"), ("openai:gpt-5.6-sol", "low")),
        ),
        patch(
            "agent.server.load_profile",
            new_callable=AsyncMock,
            return_value={
                "default_model": "anthropic:claude-opus-4-8",
                "reasoning_effort": "high",
                "default_subagent_model": "openai:gpt-5.6-sol",
                "subagent_reasoning_effort": "xhigh",
            },
        ),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[main_model, subagent_model]) as make_model,
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)

    assert captured["model"] is main_model
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    assert subagents[0]["name"] == "general-purpose"
    assert subagents[0]["model"] is subagent_model

    main_call = make_model.call_args_list[0]
    assert main_call.args == ("anthropic:claude-opus-4-8",)
    assert main_call.kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert main_call.kwargs["effort"] == "high"

    subagent_call = make_model.call_args_list[1]
    assert subagent_call.args == ("openai:gpt-5.6-sol",)
    assert subagent_call.kwargs["reasoning"] == {"effort": "xhigh", "summary": "auto"}


@pytest.mark.asyncio
async def test_agent_subagent_inherits_profile_model_override_without_explicit_pair() -> None:
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
            return_value=(("openai:gpt-5.6-sol", "medium"), ("openai:gpt-5.6-sol", "low")),
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
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)

    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    assert subagents[0]["model"] is subagent_model
    assert make_model.call_args_list[0].args == ("anthropic:claude-opus-4-8",)
    assert make_model.call_args_list[1].args == ("anthropic:claude-opus-4-8",)
    assert make_model.call_args_list[1].kwargs["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }
    assert make_model.call_args_list[1].kwargs["effort"] == "high"


@pytest.mark.asyncio
async def test_agent_gate_swaps_disabled_fable_profile_to_opus() -> None:
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
            "agent.server.resolve_github_token", new_callable=AsyncMock, return_value=("ghp", None)
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
            return_value=(("openai:gpt-5.6-sol", "medium"), ("openai:gpt-5.6-sol", "low")),
        ),
        # Profile selected Fable back when it was allowed; it's now disabled.
        patch(
            "agent.server.load_profile",
            new_callable=AsyncMock,
            return_value={
                "default_model": "anthropic:claude-fable-5",
                "reasoning_effort": "high",
            },
        ),
        patch("agent.server.get_team_fable_enabled", new_callable=AsyncMock, return_value=False),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[main_model, subagent_model]) as make_model,
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)

    # Fable was scrubbed to Opus for both main and subagent; effort preserved.
    assert make_model.call_args_list[0].args == ("anthropic:claude-opus-5",)
    assert make_model.call_args_list[0].kwargs["effort"] == "high"
    assert make_model.call_args_list[1].args == ("anthropic:claude-opus-5",)
