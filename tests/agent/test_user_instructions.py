from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.user_instructions import (
    get_user_custom_instructions,
    set_user_instructions,
)
from agent.tools.save_user_instructions import save_user_instructions


@pytest.mark.asyncio
async def test_set_user_instructions_upserts_record() -> None:
    mock_put = AsyncMock()
    with (
        patch(
            "agent.dashboard.user_instructions.get_user_instructions",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("agent.dashboard.user_instructions._client") as mock_client,
    ):
        mock_client.return_value.store.put_item = mock_put
        record = await set_user_instructions("octo", "Always run the linter.")
    assert record["login"] == "octo"
    assert record["instructions"] == "Always run the linter."
    assert record["updated_by"] == "octo"
    mock_put.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_user_custom_instructions_trims_text() -> None:
    with patch(
        "agent.dashboard.user_instructions.get_user_instructions",
        new_callable=AsyncMock,
        return_value={"instructions": "  Be terse.\n"},
    ):
        assert await get_user_custom_instructions("octo") == "Be terse."


@pytest.mark.asyncio
async def test_get_user_custom_instructions_returns_none_when_empty() -> None:
    with patch(
        "agent.dashboard.user_instructions.get_user_instructions",
        new_callable=AsyncMock,
        return_value={"instructions": "   "},
    ):
        assert await get_user_custom_instructions("octo") is None


@pytest.mark.asyncio
async def test_get_user_custom_instructions_without_login() -> None:
    assert await get_user_custom_instructions(None) is None


@pytest.mark.asyncio
async def test_save_user_instructions_requires_login() -> None:
    with patch(
        "agent.tools.save_user_instructions.get_config",
        return_value={"configurable": {}},
    ):
        result = await save_user_instructions("Always run tests.")
    assert result["ok"] is False
    assert "GitHub login" in result["error"]


@pytest.mark.asyncio
async def test_save_user_instructions_writes_record() -> None:
    mock_set = AsyncMock(return_value={"instructions": "Always run tests."})
    with (
        patch(
            "agent.tools.save_user_instructions.get_config",
            return_value={"configurable": {"github_login": "octo"}},
        ),
        patch("agent.tools.save_user_instructions.set_user_instructions", mock_set),
    ):
        result = await save_user_instructions("  Always run tests.  ")
    assert result == {"ok": True, "login": "octo", "instructions": "Always run tests."}
    mock_set.assert_awaited_once_with("octo", "Always run tests.", updated_by="open-swe")
