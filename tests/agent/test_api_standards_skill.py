from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.utils import api_standards_skill


@pytest.mark.asyncio
async def test_fetch_returns_skill_content() -> None:
    with patch.object(
        api_standards_skill,
        "_pull_api_standards_skill_sync",
        return_value="Use /v1/ prefixes.",
    ):
        content = await api_standards_skill.fetch_api_standards_skill("api-standards")
    assert content == "Use /v1/ prefixes."


@pytest.mark.asyncio
async def test_fetch_returns_none_on_error() -> None:
    def _boom(_handle: str) -> str | None:
        raise RuntimeError("no api key")

    with patch.object(api_standards_skill, "_pull_api_standards_skill_sync", side_effect=_boom):
        content = await api_standards_skill.fetch_api_standards_skill("api-standards")
    assert content is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_no_handle_configured() -> None:
    with patch.object(api_standards_skill, "API_STANDARDS_SKILL_HANDLE", ""):
        content = await api_standards_skill.fetch_api_standards_skill("")
    assert content is None
