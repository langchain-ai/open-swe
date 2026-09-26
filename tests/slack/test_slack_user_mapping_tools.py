"""Slack member lookup and admin-only mapping corrections."""

from importlib import import_module
from unittest.mock import AsyncMock

import pytest

from agent.slack.tools.lookup_user import slack_lookup_github_user
from agent.tools.manage_slack_github_mapping import manage_slack_github_mapping
from agent.users import User
from tests.support.slack_api import SlackAPI


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada,bob")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


@pytest.mark.usefixtures("registry_db")
async def test_lookup_name_and_id_only_return_linked_accounts(slack_api: SlackAPI) -> None:
    ada = await User.sign_in("github", "1", login="ada")
    await ada.link("slack", "U123", email="ada@example.com")
    slack_api.respond(
        {
            "ok": True,
            "members": [
                {"id": "U123", "name": "ada", "profile": {"display_name": "Ada Lovelace"}},
                {"id": "U234", "name": "unlinked", "profile": {"display_name": "Nobody"}},
            ],
            "response_metadata": {"next_cursor": ""},
        }
    )
    assert await slack_lookup_github_user("ada lovelace") == {
        "success": True,
        "slack_user_id": "U123",
        "github_login": "ada",
    }
    assert await slack_lookup_github_user("U123") == {
        "success": True,
        "slack_user_id": "U123",
        "github_login": "ada",
    }
    assert [method for method, _ in slack_api.calls] == ["users.list"]
    assert await slack_lookup_github_user("U234") == {
        "success": True,
        "slack_user_id": "U234",
        "github_login": None,
    }


async def test_lookup_uses_all_directory_pages(slack_api: SlackAPI) -> None:
    slack_api.respond({"ok": True, "members": [], "response_metadata": {"next_cursor": "page-2"}})
    slack_api.respond(
        {
            "ok": True,
            "members": [{"id": "U234", "name": "Bob"}],
            "response_metadata": {"next_cursor": ""},
        }
    )
    assert await slack_lookup_github_user("bob") == {
        "success": True,
        "slack_user_id": "U234",
        "github_login": None,
    }
    assert slack_api.calls[-1][1]["cursor"] == "page-2"


async def test_lookup_rejects_ambiguous_names_and_propagates_slack_errors(
    slack_api: SlackAPI,
) -> None:
    slack_api.respond(
        {
            "ok": True,
            "members": [
                {"id": "U123", "name": "Ada"},
                {"id": "U234", "profile": {"display_name": "ada"}},
            ],
        }
    )
    assert await slack_lookup_github_user("ada") == {
        "success": False,
        "error": "Ambiguous Slack name",
        "slack_user_ids": ["U123", "U234"],
    }
    slack_api.respond({"ok": False, "error": "missing_scope"})
    assert await slack_lookup_github_user("ada") == {"success": False, "error": "missing_scope"}


@pytest.mark.usefixtures("registry_db")
async def test_admin_can_move_mapping_without_inheriting_previous_email(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        import_module("agent.tools.manage_slack_github_mapping"),
        "require_admin",
        AsyncMock(return_value=None),
    )
    ada = await User.sign_in("github", "1", login="ada")
    bob = await User.sign_in("github", "2", login="bob")
    await ada.link("slack", "U123", email="ada@example.com", team_id="T1")
    slack_api.respond({"ok": True, "user": {"id": "U123", "name": "Ada"}})
    assert await manage_slack_github_mapping("U123", "bob") == {
        "success": True,
        "slack_user_id": "U123",
        "github_login": "bob",
        "previous_github_login": "ada",
    }
    moved = await User.for_identity("slack", "U123")
    assert moved is not None and moved.id == bob.id
    slack_identity = next(identity for identity in moved.identities if identity.provider == "slack")
    assert (slack_identity.email, slack_identity.team_id) == ("", "")


@pytest.mark.usefixtures("registry_db")
async def test_admin_rejects_unknown_github_user_and_inactive_slack_member(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        import_module("agent.tools.manage_slack_github_mapping"),
        "require_admin",
        AsyncMock(return_value=None),
    )
    assert (await manage_slack_github_mapping("U123", "unknown"))["success"] is False
    await User.sign_in("github", "1", login="ada")
    slack_api.respond({"ok": True, "user": {"id": "U123", "deleted": True}})
    assert (await manage_slack_github_mapping("U123", "ada"))["success"] is False
    assert await User.login_for_slack("U123") is None


async def test_non_admin_cannot_change_mapping(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        import_module("agent.tools.manage_slack_github_mapping"),
        "require_admin",
        AsyncMock(return_value="Only workspace admins can manage Slack-to-GitHub mappings."),
    )
    result = await manage_slack_github_mapping("U123", "ada")
    assert result["success"] is False
    assert slack_api.calls == []
