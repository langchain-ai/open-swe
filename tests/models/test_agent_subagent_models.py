from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import langgraph_sdk
import pytest
from langgraph.graph.state import RunnableConfig

from openswe.server import get_agent
from openswe.web.workspace_settings import WorkspaceSettings

_MODEL_DEFAULTS = {
    "default_agent_model": "openai:gpt-6.1-sol",
    "default_agent_reasoning_effort": "medium",
    "default_agent_subagent_model": "openai:gpt-6.1-sol",
    "default_agent_subagent_reasoning_effort": "low",
}


@pytest.fixture(autouse=True, params=["public", "private"])
def saved_thread_scope(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"metadata": {"visibility": request.param, "owner_login": "octocat"}}
            )
        )
    )
    monkeypatch.setattr(langgraph_sdk, "get_client", lambda: client)


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> _DummyAgent:
        self.config = config
        return self


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
        patch(
            "openswe.server.load_profile",
            new_callable=AsyncMock,
            return_value={
                "default_model": "anthropic:claude-opus-5-5",
                "reasoning_effort": "high",
            },
        ),
        patch("openswe.server.fallback_model_id_for", return_value=None),
        patch("openswe.server.make_model", side_effect=[main_model, subagent_model]) as make_model,
        patch("openswe.server.construct_system_prompt", return_value="prompt"),
        patch("openswe.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)

    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    assert subagents[0]["model"] is subagent_model
    assert make_model.call_args_list[0].args == ("anthropic:claude-opus-5-5",)
    assert make_model.call_args_list[1].args == ("anthropic:claude-opus-5-5",)
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
            return_value=WorkspaceSettings({**_MODEL_DEFAULTS, "fable_enabled": False}),
        ),
        # Profile selected Fable back when it was allowed; it's now disabled.
        patch(
            "openswe.server.load_profile",
            new_callable=AsyncMock,
            return_value={
                "default_model": "anthropic:claude-fable-5-1",
                "reasoning_effort": "high",
            },
        ),
        patch("openswe.server.fallback_model_id_for", return_value=None),
        patch("openswe.server.make_model", side_effect=[main_model, subagent_model]) as make_model,
        patch("openswe.server.construct_system_prompt", return_value="prompt"),
        patch("openswe.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)

    # Fable was scrubbed to Opus for both main and subagent; effort preserved.
    assert make_model.call_args_list[0].args == ("anthropic:claude-opus-5-5",)
    assert make_model.call_args_list[0].kwargs["effort"] == "high"
    assert make_model.call_args_list[1].args == ("anthropic:claude-opus-5-5",)
