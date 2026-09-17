from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.read_user_settings import read_user_settings
from agent.utils import thread_participants as participants


@pytest.mark.asyncio
async def test_read_user_settings_returns_redacted_participant_settings() -> None:
    with (
        patch(
            "agent.tools.read_user_settings.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch(
            "agent.tools.read_user_settings.resolve_thread_participant_logins",
            new_callable=AsyncMock,
            return_value=({"octocat"}, 1, None),
        ),
        patch(
            "agent.tools.read_user_settings.get_profile",
            new_callable=AsyncMock,
            return_value={
                "default_model": "openai:gpt-5.6-sol",
                "reasoning_effort": "high",
                "email": "private@example.com",
                "default_repo": "private/internal",
                "branch_prefix": "secret-prefix",
                "updated_at": "2026-08-16T00:00:00Z",
            },
        ),
        patch(
            "agent.tools.read_user_settings.get_user_instructions",
            new_callable=AsyncMock,
            return_value={"instructions": "Be concise."},
        ),
        patch(
            "agent.tools.read_user_settings.get_notion_status",
            new_callable=AsyncMock,
            return_value={"notion": {"connected": True}},
        ),
        patch(
            "agent.tools.read_user_settings.actor_is_admin",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        result = await read_user_settings()

    assert result == {
        "success": True,
        "participants": [
            {
                "login": "octocat",
                "profile": {
                    "default_model": "openai:gpt-5.6-sol",
                    "reasoning_effort": "high",
                },
                "instructions": "Be concise.",
                "connections": {
                    "notion": {"connected": True},
                },
            }
        ],
        "unresolved_participant_count": 1,
    }
    rendered = repr(result).lower()
    assert "token" not in rendered
    assert "private@example.com" not in rendered
    assert "private/internal" not in rendered
    assert "secret-prefix" not in rendered
    assert "updated_at" not in rendered
    assert "workspace" not in result


@pytest.mark.asyncio
async def test_read_user_settings_includes_workspace_settings_for_admin() -> None:
    workspace_settings = {
        "review_draft_prs": True,
        "pr_summaries": False,
        "review_trace_links": True,
        "model_routing_enabled": False,
        "gateway_enabled": None,
        "fable_enabled": False,
        "expedited_review_enabled": True,
    }
    with (
        patch(
            "agent.tools.read_user_settings.get_config",
            return_value={"configurable": {"thread_id": "thread-1", "github_login": "octocat"}},
        ),
        patch(
            "agent.tools.read_user_settings.resolve_thread_participant_logins",
            new_callable=AsyncMock,
            return_value=({"octocat"}, 0, None),
        ),
        patch(
            "agent.tools.read_user_settings.get_profile", new_callable=AsyncMock, return_value={}
        ),
        patch(
            "agent.tools.read_user_settings.get_user_instructions",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.tools.read_user_settings.get_notion_status",
            new_callable=AsyncMock,
            return_value={"notion": {"connected": False}},
        ),
        patch(
            "agent.tools.read_user_settings.actor_is_admin",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "agent.tools.read_user_settings.cached_workspace_settings",
            new_callable=AsyncMock,
            return_value=workspace_settings,
        ),
    ):
        result = await read_user_settings()

    assert result["workspace"] == workspace_settings


@pytest.mark.asyncio
async def test_read_user_settings_fails_before_settings_reads() -> None:
    profile = AsyncMock()
    with (
        patch(
            "agent.tools.read_user_settings.get_config",
            return_value={"configurable": {"thread_id": "thread-1"}},
        ),
        patch(
            "agent.tools.read_user_settings.resolve_thread_participant_logins",
            new_callable=AsyncMock,
            return_value=(None, 0, "Could not verify the active thread"),
        ),
        patch("agent.tools.read_user_settings.get_profile", profile),
    ):
        result = await read_user_settings()

    assert result == {"success": False, "error": "Could not verify the active thread"}
    profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_participants_include_broadcasts_and_exclude_system_messages() -> None:
    messages = [
        {"user": "U1"},
        {"user": "UBROADCAST", "subtype": "thread_broadcast"},
        {"user": "UBOT", "bot_id": "B1"},
        {"user": "UEDIT", "subtype": "message_changed"},
        {"user": "U2"},
    ]

    async def login_for_slack(user_id: str) -> str | None:
        return {
            "U1": "octocat",
            "UBROADCAST": "broadcaster",
            "U2": None,
        }.get(user_id)

    async def for_login(provider: str, login: str) -> SimpleNamespace:
        return SimpleNamespace(github_login=login)

    with (
        patch.object(participants.User, "login_for_slack", side_effect=login_for_slack),
        patch.object(participants.User, "for_login", side_effect=for_login),
    ):
        logins, unresolved = await participants._mapped_slack_logins(messages)

    assert logins == {"octocat", "broadcaster"}
    assert unresolved == 1


@pytest.mark.asyncio
async def test_linear_participants_use_verified_email_mappings() -> None:
    async def login_for_email(email: str) -> str | None:
        return {"octo@example.com": "octocat", "missing@example.com": None}.get(email)

    with (
        patch.object(participants.User, "login_for_email", side_effect=login_for_email),
        patch.object(
            participants.User,
            "for_login",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(github_login="octocat"),
        ),
    ):
        logins, unresolved = await participants._mapped_email_logins(
            {"octo@example.com", "missing@example.com"}
        )

    assert logins == {"octocat"}
    assert unresolved == 1


@pytest.mark.asyncio
async def test_dashboard_participants_are_read_from_trusted_metadata() -> None:
    class Threads:
        async def get(self, thread_id: str) -> dict[str, object]:
            assert thread_id == "thread-1"
            return {
                "metadata": {
                    "source": "dashboard",
                    "github_login": "owner",
                    "participant_logins": ["owner", "teammate"],
                }
            }

    class Client:
        threads = Threads()

    async def for_login(provider: str, login: str) -> SimpleNamespace:
        return SimpleNamespace(github_login=login)

    with (
        patch.object(participants, "get_client", return_value=Client()),
        patch.object(participants.User, "for_login", side_effect=for_login),
    ):
        logins, unresolved, error = await participants.resolve_thread_participant_logins(
            {"configurable": {"thread_id": "thread-1", "source": "dashboard"}}
        )

    assert error is None
    assert unresolved == 0
    assert logins == {"owner", "teammate"}
