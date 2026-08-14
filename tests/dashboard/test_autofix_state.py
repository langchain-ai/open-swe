"""Unit tests for per-PR auto-fix opt-out state."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.dashboard import autofix_state


@pytest.mark.asyncio
async def test_pr_cycle_count_uses_sibling_state_record() -> None:
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
        assert await autofix_state.get_pr_autofix_cycle_count("O", "R", 5) == 0
        await autofix_state.set_pr_autofix_disabled("O", "R", 5, True)
        await autofix_state.set_pr_autofix_cycle_count("O", "R", 5, 1)
        assert await autofix_state.is_pr_autofix_disabled("o", "r", 5) is True
        assert await autofix_state.get_pr_autofix_cycle_count("o", "r", 5) == 1

    namespace = ("autofix_pr_state",)
    assert store[(namespace, "o/r#5")]["disabled"] is True
    assert store[(namespace, "o/r#5:review_autofix_cycles")]["cycle_count"] == 1
