import pytest
from pydantic import ValidationError

from agent.dashboard.workspace_settings import (
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
)

_REVIEWER_SUBAGENT_PAIR = ("openai:gpt-5.6-sol", "low")
_GROUPING_PAIR = ("google_genai:gemini-3.8-flash", "low")
_ROUTING_PAIRS = {
    "fast": ("google_genai:gemini-3.8-flash", "low"),
    "balanced": ("openai:gpt-5.6-sol", "medium"),
    "performance": ("anthropic:claude-opus-5", "high"),
}


def _settings(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "default_reviewer_subagent_model": _REVIEWER_SUBAGENT_PAIR[0],
        "default_reviewer_subagent_reasoning_effort": _REVIEWER_SUBAGENT_PAIR[1],
        "default_grouping_model": None,
        "default_grouping_reasoning_effort": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_grouping_inherits_reviewer_subagent_when_unset() -> None:
    assert WorkspaceSettings(_settings()).default_grouping_model == _REVIEWER_SUBAGENT_PAIR


@pytest.mark.asyncio
async def test_grouping_uses_configured_model_when_set() -> None:
    assert (
        WorkspaceSettings(
            _settings(
                default_grouping_model=_GROUPING_PAIR[0],
                default_grouping_reasoning_effort=_GROUPING_PAIR[1],
            )
        ).default_grouping_model
        == _GROUPING_PAIR
    )


@pytest.mark.asyncio
async def test_grouping_inherits_when_configured_model_invalid() -> None:
    assert (
        WorkspaceSettings(
            _settings(
                default_grouping_model="bogus:model",
                default_grouping_reasoning_effort="high",
            )
        ).default_grouping_model
        == _REVIEWER_SUBAGENT_PAIR
    )


@pytest.mark.asyncio
async def test_agent_routing_uses_configured_model_pairs() -> None:
    settings = {
        f"default_agent_routing_{tier}_{suffix}": value
        for tier, pair in _ROUTING_PAIRS.items()
        for suffix, value in zip(("model", "reasoning_effort"), pair, strict=True)
    }
    assert WorkspaceSettings(settings).agent_routing_models == _ROUTING_PAIRS


def test_workspace_settings_update_accepts_routing_pairs() -> None:
    update = WorkspaceSettingsUpdate(
        **{
            f"default_agent_routing_{tier}_{suffix}": value
            for tier, pair in _ROUTING_PAIRS.items()
            for suffix, value in zip(("model", "reasoning_effort"), pair, strict=True)
        }
    )
    assert update.default_agent_routing_fast_model == _ROUTING_PAIRS["fast"][0]
    assert (
        update.default_agent_routing_performance_reasoning_effort
        == _ROUTING_PAIRS["performance"][1]
    )


def test_workspace_settings_update_accepts_grouping_pair() -> None:
    update = WorkspaceSettingsUpdate(
        default_grouping_model=_GROUPING_PAIR[0],
        default_grouping_reasoning_effort=_GROUPING_PAIR[1],
    )
    assert update.default_grouping_model == _GROUPING_PAIR[0]
    assert update.default_grouping_reasoning_effort == _GROUPING_PAIR[1]


def test_workspace_settings_update_rejects_grouping_effort_without_model() -> None:
    with pytest.raises(ValidationError):
        WorkspaceSettingsUpdate(default_grouping_reasoning_effort="high")


def test_workspace_settings_update_rejects_unsupported_grouping_effort() -> None:
    with pytest.raises(ValidationError):
        WorkspaceSettingsUpdate(
            default_grouping_model=_GROUPING_PAIR[0],
            default_grouping_reasoning_effort="max",
        )
