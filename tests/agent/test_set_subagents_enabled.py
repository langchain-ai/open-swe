from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.set_subagents_enabled import set_subagents_enabled


@pytest.mark.asyncio
async def test_set_subagents_enabled_requires_triggering_user() -> None:
    with patch(
        "agent.tools.set_subagents_enabled.get_config",
        return_value={"configurable": {}},
    ):
        result = await set_subagents_enabled(False)

    assert result["ok"] is False
    assert "GitHub login" in result["error"]


@pytest.mark.asyncio
async def test_set_subagents_enabled_writes_inverse_for_triggering_user() -> None:
    set_disabled = AsyncMock()
    with (
        patch(
            "agent.tools.set_subagents_enabled.get_config",
            return_value={"configurable": {"github_login": "octocat"}},
        ),
        patch("agent.tools.set_subagents_enabled.set_disable_subagents", set_disabled),
    ):
        result = await set_subagents_enabled(False)

    assert result == {"ok": True, "login": "octocat", "subagents_enabled": False}
    set_disabled.assert_awaited_once_with("octocat", True)


@pytest.mark.asyncio
async def test_set_subagents_enabled_enables_subagents() -> None:
    set_disabled = AsyncMock()
    with (
        patch(
            "agent.tools.set_subagents_enabled.get_config",
            return_value={"configurable": {"github_login": "octocat"}},
        ),
        patch("agent.tools.set_subagents_enabled.set_disable_subagents", set_disabled),
    ):
        result = await set_subagents_enabled(True)

    assert result["subagents_enabled"] is True
    set_disabled.assert_awaited_once_with("octocat", False)
