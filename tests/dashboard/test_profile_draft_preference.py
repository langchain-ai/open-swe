from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.profiles import ProfileUpdate, normalize_profile_for_response, upsert_profile


@pytest.mark.asyncio
async def test_omitted_draft_preference_preserves_existing_value() -> None:
    update = ProfileUpdate(default_model="openai:gpt-5.6-sol", reasoning_effort="medium")
    put_item = AsyncMock()

    with (
        patch(
            "agent.dashboard.profiles.get_profile",
            new_callable=AsyncMock,
            return_value={"draft_prs": False},
        ),
        patch("agent.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["draft_prs"] is False
    assert put_item.await_args is not None
    assert put_item.await_args.args[2]["draft_prs"] is False


@pytest.mark.asyncio
async def test_adaptive_model_routing_is_persisted() -> None:
    update = ProfileUpdate(
        default_model="openai:gpt-5.6-sol",
        reasoning_effort="medium",
        adaptive_model_routing=True,
    )
    put_item = AsyncMock()

    with (
        patch("agent.dashboard.profiles.get_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["adaptive_model_routing"] is True
    assert put_item.await_args is not None
    assert put_item.await_args.args[2]["adaptive_model_routing"] is True


@pytest.mark.asyncio
async def test_explicit_draft_preference_is_persisted() -> None:
    update = ProfileUpdate(
        default_model="openai:gpt-5.6-sol",
        reasoning_effort="medium",
        draft_prs=True,
    )
    put_item = AsyncMock()

    with (
        patch("agent.dashboard.profiles.get_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["draft_prs"] is True
    assert put_item.await_args is not None
    assert put_item.await_args.args[2]["draft_prs"] is True


def test_profile_response_hides_legacy_create_prs_setting() -> None:
    profile = normalize_profile_for_response({"create_prs": True})

    assert "create_prs" not in profile


@pytest.mark.asyncio
async def test_profile_save_removes_legacy_create_prs_setting() -> None:
    update = ProfileUpdate(default_model="openai:gpt-5.6-sol", reasoning_effort="medium")
    put_item = AsyncMock()

    with (
        patch(
            "agent.dashboard.profiles.get_profile",
            new_callable=AsyncMock,
            return_value={"create_prs": True},
        ),
        patch("agent.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert "create_prs" not in profile
    assert put_item.await_args is not None
    assert "create_prs" not in put_item.await_args.args[2]
