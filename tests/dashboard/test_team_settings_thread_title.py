from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.team_settings import (
    DEFAULT_THREAD_TITLE_MODEL,
    DEFAULT_THREAD_TITLE_REASONING_EFFORT,
    get_team_default_thread_title_model,
)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({}, (DEFAULT_THREAD_TITLE_MODEL, DEFAULT_THREAD_TITLE_REASONING_EFFORT)),
        (
            {
                "default_thread_title_model": "google_genai:gemini-3.7-flash",
                "default_thread_title_reasoning_effort": "minimal",
            },
            ("google_genai:gemini-3.7-flash", "minimal"),
        ),
    ],
)
@pytest.mark.asyncio
async def test_thread_title_model_default_and_override(
    settings: dict[str, str], expected: tuple[str, str]
) -> None:
    with patch(
        "agent.dashboard.team_settings.get_team_settings",
        new_callable=AsyncMock,
        return_value=settings,
    ):
        assert await get_team_default_thread_title_model() == expected
