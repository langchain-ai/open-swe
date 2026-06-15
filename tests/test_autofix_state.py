"""Unit tests for per-PR auto-fix opt-out state and team settings accessor."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.dashboard import autofix_state, team_settings


@pytest.mark.asyncio
async def test_set_and_check_pr_disabled() -> None:
    store: dict[tuple[Any, ...], Any] = {}
    client = MagicMock()

    async def put_item(ns: list[str], key: str, value: dict[str, Any]) -> None:
        store[(tuple(ns), key)] = value

    async def get_item(ns: list[str], key: str) -> dict[str, Any] | None:
        value = store.get((tuple(ns), key))
        return {"value": value} if value is not None else None

    client.store.put_item = AsyncMock(side_effect=put_item)
    client.store.get_item = AsyncMock(side_effect=get_item)

    with patch.object(autofix_state, "get_client", return_value=client):
        assert await autofix_state.is_pr_autofix_disabled("O", "R", 5) is False
        await autofix_state.set_pr_autofix_disabled("O", "R", 5, True)
        assert await autofix_state.is_pr_autofix_disabled("o", "r", 5) is True
        await autofix_state.set_pr_autofix_disabled("o", "r", 5, False)
        assert await autofix_state.is_pr_autofix_disabled("o", "r", 5) is False


@pytest.mark.asyncio
async def test_get_autofix_settings_normalizes() -> None:
    with patch.object(
        team_settings,
        "get_team_settings",
        AsyncMock(
            return_value={
                "autofix_mode": "bogus",
                "autofix_severity_threshold": "high",
                "trigger_mode": "weird",
            }
        ),
    ):
        settings = await team_settings.get_autofix_settings()
    assert settings == {
        "autofix_mode": "off",
        "autofix_severity_threshold": "high",
        "trigger_mode": "every_push",
    }


@pytest.mark.asyncio
async def test_is_autofix_enabled() -> None:
    with patch.object(
        team_settings,
        "get_team_settings",
        AsyncMock(return_value={"autofix_mode": "high"}),
    ):
        assert await team_settings.is_autofix_enabled() is True
    with patch.object(
        team_settings,
        "get_team_settings",
        AsyncMock(return_value={"autofix_mode": "off"}),
    ):
        assert await team_settings.is_autofix_enabled() is False
