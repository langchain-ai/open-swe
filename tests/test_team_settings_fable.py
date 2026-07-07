from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.options import FABLE_MODEL_IDS
from agent.dashboard.team_settings import TeamSettingsUpdate, get_team_fable_enabled

_FABLE = "anthropic:claude-fable-5"

# --- accessor: get_team_fable_enabled (async, patched store) ---


@pytest.mark.asyncio
async def test_fable_enabled_defaults_false_when_absent() -> None:
    # Legacy record with no fable_enabled key -> off.
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={},
    ):
        assert await get_team_fable_enabled() is False


@pytest.mark.asyncio
async def test_fable_enabled_true_when_set() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"fable_enabled": True},
    ):
        assert await get_team_fable_enabled() is True


@pytest.mark.asyncio
async def test_fable_enabled_false_for_non_bool_value() -> None:
    # Fail-closed: any non-bool (e.g. a stray string) resolves to False.
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"fable_enabled": "true"},
    ):
        assert await get_team_fable_enabled() is False


# --- validation: TeamSettingsUpdate (sync) ---


def test_update_defaults_fable_disabled() -> None:
    assert TeamSettingsUpdate().fable_enabled is False


def test_update_coerces_fable_model_when_disabled() -> None:
    # Disabling Fable must never fail: a lingering Fable default is swapped for a
    # safe non-Fable fallback (effort preserved) rather than rejected.
    update = TeamSettingsUpdate(
        default_agent_model=_FABLE,
        default_agent_reasoning_effort="high",
    )
    assert update.default_agent_model not in FABLE_MODEL_IDS
    assert update.default_agent_reasoning_effort == "high"


def test_update_coerces_fable_in_any_role_when_disabled() -> None:
    # Same coercion applies to every model field, e.g. review chat.
    update = TeamSettingsUpdate(
        default_chat_model=_FABLE,
        default_chat_reasoning_effort="high",
    )
    assert update.default_chat_model not in FABLE_MODEL_IDS
    assert update.default_chat_reasoning_effort == "high"


def test_disable_transition_coerces_all_fable_defaults() -> None:
    # The kill-switch flow: an admin who had picked Fable everywhere flips the
    # toggle off, and the UI re-sends the whole settings blob with the Fable
    # defaults still attached. The update must succeed and strip every Fable id.
    update = TeamSettingsUpdate(
        fable_enabled=False,
        default_agent_model=_FABLE,
        default_agent_reasoning_effort="high",
        default_agent_subagent_model=_FABLE,
        default_agent_subagent_reasoning_effort="high",
        default_reviewer_model=_FABLE,
        default_reviewer_reasoning_effort="high",
        default_reviewer_subagent_model=_FABLE,
        default_reviewer_subagent_reasoning_effort="high",
        default_grouping_model=_FABLE,
        default_grouping_reasoning_effort="high",
        default_chat_model=_FABLE,
        default_chat_reasoning_effort="high",
    )
    for field in (
        "default_agent_model",
        "default_agent_subagent_model",
        "default_reviewer_model",
        "default_reviewer_subagent_model",
        "default_grouping_model",
        "default_chat_model",
    ):
        assert getattr(update, field) not in FABLE_MODEL_IDS, field


def test_update_leaves_non_fable_defaults_untouched_when_disabled() -> None:
    # Coercion only rewrites Fable ids; other selections pass through unchanged.
    update = TeamSettingsUpdate(
        default_agent_model="openai:gpt-5.5",
        default_agent_reasoning_effort="medium",
    )
    assert update.default_agent_model == "openai:gpt-5.5"
    assert update.default_agent_reasoning_effort == "medium"


def test_update_accepts_fable_model_when_enabled() -> None:
    update = TeamSettingsUpdate(
        fable_enabled=True,
        default_agent_model=_FABLE,
        default_agent_reasoning_effort="high",
    )
    assert update.fable_enabled is True
    assert update.default_agent_model == _FABLE
