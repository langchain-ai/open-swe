from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.dashboard.team_settings import (
    TeamSettingsUpdate,
    get_team_autofix_settings,
    upsert_team_settings,
)

# --- accessor: get_team_autofix_settings (async, patched settings) ---


@pytest.mark.asyncio
async def test_autofix_defaults_off_when_absent() -> None:
    # Legacy record with no autofix keys -> disabled, medium threshold.
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={},
    ):
        assert await get_team_autofix_settings() == (False, "medium")


@pytest.mark.asyncio
async def test_autofix_enabled_with_threshold() -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"autofix_enabled": True, "autofix_severity_threshold": "high"},
    ):
        assert await get_team_autofix_settings() == (True, "high")


@pytest.mark.asyncio
async def test_autofix_invalid_values_fail_closed() -> None:
    # Fail-closed: non-bool enabled reads as off, unknown threshold as medium.
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value={"autofix_enabled": "true", "autofix_severity_threshold": "extreme"},
    ):
        assert await get_team_autofix_settings() == (False, "medium")


# --- upsert: unrelated saves must not erase the opt-in ---


def _put_capture() -> tuple[MagicMock, AsyncMock]:
    client = MagicMock()
    put = AsyncMock()
    client.store.put_item = put
    return client, put


@pytest.mark.asyncio
async def test_upsert_without_autofix_fields_preserves_stored_opt_in() -> None:
    client, put = _put_capture()
    with (
        patch(
            "agent.dashboard.team_settings._get_stored_team_settings",
            new_callable=AsyncMock,
            return_value={"autofix_enabled": True, "autofix_severity_threshold": "high"},
        ),
        patch("agent.dashboard.team_settings._client", return_value=client),
    ):
        value = await upsert_team_settings(TeamSettingsUpdate())

    put.assert_awaited_once()
    assert value["autofix_enabled"] is True
    assert value["autofix_severity_threshold"] == "high"


@pytest.mark.asyncio
async def test_upsert_sets_autofix_fields_explicitly() -> None:
    client, put = _put_capture()
    with (
        patch(
            "agent.dashboard.team_settings._get_stored_team_settings",
            new_callable=AsyncMock,
            return_value={"autofix_enabled": True, "autofix_severity_threshold": "high"},
        ),
        patch("agent.dashboard.team_settings._client", return_value=client),
    ):
        value = await upsert_team_settings(
            TeamSettingsUpdate(autofix_enabled=False, autofix_severity_threshold="low")
        )

    put.assert_awaited_once()
    assert value["autofix_enabled"] is False
    assert value["autofix_severity_threshold"] == "low"


def test_update_rejects_unknown_threshold() -> None:
    with pytest.raises(ValueError):
        TeamSettingsUpdate(autofix_severity_threshold="extreme")  # type: ignore[arg-type]
