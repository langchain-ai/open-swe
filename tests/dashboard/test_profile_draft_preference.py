from unittest.mock import AsyncMock, patch

import pytest

from openswe.dashboard.profiles import ProfileUpdate, upsert_profile
from tests.conftest import FakeUserRecords


@pytest.mark.asyncio
async def test_omitted_draft_preference_preserves_existing_value(
    user_records: FakeUserRecords,
) -> None:
    update = ProfileUpdate(default_model="openai:gpt-5.6-sol", reasoning_effort="medium")
    with (
        patch(
            "openswe.dashboard.profiles.get_profile",
            new_callable=AsyncMock,
            return_value={
                "draft_prs": False,
                "model_routing_enabled": False,
                "recent_thread_context_enabled": True,
                "slack_onboarding_dismissed": True,
            },
        ),
    ):
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["draft_prs"] is False
    assert profile["model_routing_enabled"] is False
    assert profile["recent_thread_context_enabled"] is True
    assert profile["slack_onboarding_dismissed"] is True
    assert user_records.get("profile", "octocat") == profile


@pytest.mark.asyncio
async def test_explicit_draft_preference_is_persisted(user_records: FakeUserRecords) -> None:
    update = ProfileUpdate(
        default_model="openai:gpt-5.6-sol",
        reasoning_effort="medium",
        draft_prs=True,
    )
    with (
        patch("openswe.dashboard.profiles.get_profile", new_callable=AsyncMock, return_value=None),
    ):
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["draft_prs"] is True
    assert user_records.get("profile", "octocat") == profile
