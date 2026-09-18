from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.profiles import (
    ProfileUpdate,
    normalize_profile_for_response,
    set_disable_subagents,
    upsert_profile,
)


@pytest.mark.asyncio
async def test_omitted_draft_preference_preserves_existing_value() -> None:
    update = ProfileUpdate(default_model="openai:gpt-5.6-sol", reasoning_effort="medium")
    put_item = AsyncMock()

    with (
        patch(
            "agent.dashboard.profiles.get_profile",
            new_callable=AsyncMock,
            return_value={"draft_prs": False, "model_routing_enabled": False},
        ),
        patch("agent.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["draft_prs"] is False
    assert profile["model_routing_enabled"] is False
    assert put_item.await_args is not None
    assert put_item.await_args.args[2]["draft_prs"] is False
    assert put_item.await_args.args[2]["model_routing_enabled"] is False


@pytest.mark.asyncio
async def test_omitted_disable_subagents_preserves_existing_value() -> None:
    update = ProfileUpdate(default_model="openai:gpt-5.6-sol", reasoning_effort="medium")
    put_item = AsyncMock()

    with (
        patch(
            "agent.dashboard.profiles.get_profile",
            new_callable=AsyncMock,
            return_value={"disable_subagents": True},
        ),
        patch("agent.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["disable_subagents"] is True
    assert put_item.await_args is not None
    assert put_item.await_args.args[2]["disable_subagents"] is True


@pytest.mark.asyncio
async def test_disable_subagents_defaults_false_and_persists_explicit_value() -> None:
    put_item = AsyncMock()

    with (
        patch("agent.dashboard.profiles.get_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        default_profile = await upsert_profile(
            "octocat",
            "octocat@example.com",
            ProfileUpdate(default_model="openai:gpt-5.6-sol", reasoning_effort="medium"),
        )
        disabled_profile = await upsert_profile(
            "octocat",
            "octocat@example.com",
            ProfileUpdate(
                default_model="openai:gpt-5.6-sol",
                reasoning_effort="medium",
                disable_subagents=True,
            ),
        )

    assert default_profile["disable_subagents"] is False
    assert disabled_profile["disable_subagents"] is True


@pytest.mark.asyncio
async def test_set_disable_subagents_preserves_existing_profile() -> None:
    put_item = AsyncMock()

    with (
        patch(
            "agent.dashboard.profiles.get_profile",
            new_callable=AsyncMock,
            return_value={"login": "octocat", "default_model": "openai:gpt-5.6-sol"},
        ),
        patch("agent.dashboard.profiles.now_iso", return_value="now"),
        patch("agent.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await set_disable_subagents("octocat", True)

    assert profile == {
        "login": "octocat",
        "default_model": "openai:gpt-5.6-sol",
        "disable_subagents": True,
        "updated_at": "now",
    }
    put_item.assert_awaited_once_with(["profiles"], "octocat", profile)


@pytest.mark.asyncio
async def test_explicit_model_routing_preference_is_persisted() -> None:
    update = ProfileUpdate(
        default_model="openai:gpt-5.6-sol",
        reasoning_effort="medium",
        model_routing_enabled=False,
    )
    put_item = AsyncMock()

    with (
        patch("agent.dashboard.profiles.get_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.store.store_client") as client,
    ):
        client.return_value.store.put_item = put_item
        profile = await upsert_profile("octocat", "octocat@example.com", update)

    assert profile["model_routing_enabled"] is False
    assert put_item.await_args is not None
    assert put_item.await_args.args[2]["model_routing_enabled"] is False


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
