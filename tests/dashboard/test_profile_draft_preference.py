from unittest.mock import AsyncMock, patch

import pytest

from openswe.dashboard.profiles import ProfileUpdate, upsert_profile


@pytest.mark.asyncio
async def test_omitted_draft_preference_preserves_existing_value() -> None:
    update = ProfileUpdate(default_model="openai:gpt-5.6-sol", reasoning_effort="medium")
    put_item = AsyncMock()

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
        patch("openswe.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["draft_prs"] is False
    assert profile["model_routing_enabled"] is False
    assert profile["recent_thread_context_enabled"] is True
    assert profile["slack_onboarding_dismissed"] is True
    assert put_item.await_args is not None
    assert put_item.await_args.args[2]["draft_prs"] is False
    assert put_item.await_args.args[2]["model_routing_enabled"] is False
    assert put_item.await_args.args[2]["recent_thread_context_enabled"] is True


@pytest.mark.asyncio
async def test_explicit_draft_preference_is_persisted() -> None:
    update = ProfileUpdate(
        default_model="openai:gpt-5.6-sol",
        reasoning_effort="medium",
        draft_prs=True,
    )
    put_item = AsyncMock()

    with (
        patch("openswe.dashboard.profiles.get_profile", new_callable=AsyncMock, return_value=None),
        patch("openswe.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["draft_prs"] is True
    assert put_item.await_args is not None
    assert put_item.await_args.args[2]["draft_prs"] is True
