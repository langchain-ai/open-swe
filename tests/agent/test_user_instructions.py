from collections.abc import Callable
from unittest.mock import AsyncMock, patch

import pytest

from openswe.tools.save_user_instructions import save_user_instructions


@pytest.mark.asyncio
async def test_save_user_instructions_requires_login(
    grant_tool_access: Callable[..., None],
) -> None:
    grant_tool_access(private=True, owner=True)
    with patch(
        "openswe.tools.save_user_instructions.get_config",
        return_value={"configurable": {}},
    ):
        result = await save_user_instructions("Always run tests.")
    assert result["ok"] is False
    assert "GitHub login" in result["error"]


@pytest.mark.asyncio
async def test_save_user_instructions_writes_record(grant_tool_access: Callable[..., None]) -> None:
    grant_tool_access(private=True, owner=True)
    mock_set = AsyncMock(return_value={"instructions": "Always run tests."})
    with (
        patch(
            "openswe.tools.save_user_instructions.get_config",
            return_value={"configurable": {"github_login": "octo"}},
        ),
        patch("openswe.tools.save_user_instructions.set_user_instructions", mock_set),
    ):
        result = await save_user_instructions("  Always run tests.  ")
    assert result["ok"] is True
    assert result["login"] == "octo"
    assert result["instructions"] == "Always run tests."
    # The updated text is delivered as a new message, never by rewriting the
    # thread's system prompt, which would invalidate its prefix cache.
    assert "Always run tests." in result["reminder"]
    assert result["reminder"].startswith("<system-reminder>")
    mock_set.assert_awaited_once_with("octo", "Always run tests.", updated_by="open-swe")
