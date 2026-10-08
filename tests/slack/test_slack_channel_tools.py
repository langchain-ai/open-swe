from unittest.mock import AsyncMock

import pytest

from openswe import tools
from openswe.run_config import RunConfig
from openswe.users import User
from tests.support.slack_api import SlackAPI


async def test_list_channels_keeps_cursor_on_empty_page(slack_api: SlackAPI) -> None:
    slack_api.respond({"ok": True, "channels": [], "response_metadata": {"next_cursor": "more"}})
    result = await tools.slack_list_channels()
    assert result == {"success": True, "channels": [], "next_cursor": "more"}


@pytest.fixture
def member_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        RunConfig,
        "from_runtime",
        lambda: RunConfig.parse({"slack_thread": {"channel_id": "C123", "thread_ts": "1.0"}}),
    )


async def test_list_members_preserves_pagination_and_only_exposes_public_identity(
    slack_api: SlackAPI, member_run: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(User, "login_for_slack", AsyncMock(return_value="octocat"))
    slack_api.respond({"ok": True, "channel": {"id": "C123", "is_private": True}})
    slack_api.respond({"ok": True, "members": [], "response_metadata": {"next_cursor": "more"}})
    assert await tools.slack_list_channel_members("C123") == {
        "success": True,
        "channel_id": "C123",
        "members": [],
        "next_cursor": "more",
    }
    slack_api.respond({"ok": True, "channel": {"id": "C123", "is_private": True}})
    slack_api.respond({"ok": True, "members": ["U123"]})
    slack_api.respond(
        {"ok": True, "user": {"profile": {"display_name": "Ada", "email": "private@example.com"}}}
    )
    assert await tools.slack_list_channel_members("C123", cursor="more") == {
        "success": True,
        "channel_id": "C123",
        "members": [{"id": "U123", "name": "Ada", "github_login": "octocat"}],
        "next_cursor": "",
    }
    assert slack_api.calls[3][1]["cursor"] == "more"


@pytest.mark.parametrize("private,external", [(False, False), (True, False), (False, True)])
async def test_list_members_restricts_other_channels(
    slack_api: SlackAPI, member_run: None, private: bool, external: bool
) -> None:
    slack_api.respond(
        {
            "ok": True,
            "channel": {
                "id": "C456",
                "is_channel": True,
                "is_private": private,
                "is_ext_shared": external,
                "is_pending_ext_shared": False,
            },
        }
    )
    slack_api.respond({"ok": True, "members": []})
    result = await tools.slack_list_channel_members("C456")
    assert result["success"] is (not private and not external)
    assert ("conversations.members" in [method for method, _ in slack_api.calls]) is (
        not private and not external
    )


@pytest.mark.parametrize(
    "payload",
    [{"members": [42]}, {"members": [], "response_metadata": {"next_cursor": 42}}],
)
async def test_list_members_rejects_malformed_pages(
    slack_api: SlackAPI, member_run: None, payload: dict[str, object]
) -> None:
    slack_api.respond({"ok": True, "channel": {"id": "C123"}})
    slack_api.respond({"ok": True, **payload})
    assert await tools.slack_list_channel_members("C123") == {
        "success": False,
        "error": "invalid_slack_response",
    }


async def test_list_members_keeps_unknown_identity_when_profile_lookup_fails(
    slack_api: SlackAPI, member_run: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(User, "login_for_slack", AsyncMock(return_value=None))
    slack_api.respond({"ok": True, "channel": {"id": "C123"}})
    slack_api.respond({"ok": True, "members": ["U123"]})
    slack_api.respond({"ok": False, "error": "user_not_found"})
    result = await tools.slack_list_channel_members("C123")
    assert result == {
        "success": True,
        "channel_id": "C123",
        "members": [{"id": "U123", "name": "U123", "github_login": None}],
        "next_cursor": "",
    }


async def test_list_members_reports_rate_limit_instead_of_empty_page(
    slack_api: SlackAPI, member_run: None
) -> None:
    slack_api.respond({"ok": True, "channel": {"id": "C123"}})
    slack_api.respond(
        {"ok": False, "error": "ratelimited"}, status=429, headers={"Retry-After": "30"}
    )
    assert await tools.slack_list_channel_members("C123") == {
        "success": False,
        "error": "rate_limited: 30",
    }


async def test_post_channel_message_requires_active_channel_membership(
    slack_api: SlackAPI,
) -> None:
    slack_api.respond(
        {"ok": True, "channels": [{"id": "C456", "name": "other", "is_private": False}]}
    )
    result = await tools.slack_post_message("C123", "hello")
    assert result == {"success": False, "error": "not_in_channel"}
    assert [method for method, _ in slack_api.calls] == ["users.conversations"]


@pytest.mark.parametrize("source_channel", ["C123", "C456"])
async def test_post_channel_message_finds_membership_after_empty_page(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch, source_channel: str
) -> None:
    from openswe.slack.tools import channels

    monkeypatch.setattr(
        RunConfig, "from_runtime", lambda: RunConfig.parse({"thread_id": "agent-thread"})
    )
    monkeypatch.setattr(
        channels, "run_slack_location", AsyncMock(return_value=(source_channel, "1.0"))
    )
    slack_api.respond({"ok": True, "channels": [], "response_metadata": {"next_cursor": "more"}})
    slack_api.respond(
        {"ok": True, "channels": [{"id": "C123", "name": "target", "is_private": False}]}
    )
    if source_channel != "C123":
        slack_api.respond({"ok": True, "permalink": "https://example.slack.com/archives/C456/p10"})
    slack_api.respond({"ok": True, "ts": "1700000000.000123"})
    result = await tools.slack_post_message("C123", "hello")
    assert result["success"] is True
    assert slack_api.calls[1][1]["cursor"] == "more"
    assert slack_api.calls[-1][0] == "chat.postMessage"
    blocks = slack_api.calls[-1][1]["blocks"]
    assert ("https://example.slack.com/archives/C456/p10" in str(blocks)) is (
        source_channel != "C123"
    )


async def test_post_channel_message_stops_on_repeated_membership_cursor(
    slack_api: SlackAPI,
) -> None:
    for _ in range(2):
        slack_api.respond(
            {"ok": True, "channels": [], "response_metadata": {"next_cursor": "same"}}
        )
    result = await tools.slack_post_message("C123", "hello")
    assert result == {"success": False, "error": "invalid_slack_response"}
    assert len(slack_api.calls) == 2


@pytest.mark.parametrize(
    "error,status,expected",
    [("ratelimited", 429, "rate_limited: 30"), ("not_in_channel", 200, "not_in_channel")],
)
async def test_post_channel_message_reports_slack_failure_without_retry(
    slack_api: SlackAPI, member_run: None, error: str, status: int, expected: str
) -> None:
    slack_api.respond(
        {"ok": True, "channels": [{"id": "C123", "name": "target", "is_private": False}]}
    )
    slack_api.respond({"ok": False, "error": error}, status=status, headers={"Retry-After": "30"})
    result = await tools.slack_post_message("C123", "hello")
    assert result == {"success": False, "error": expected}
    assert len(slack_api.calls) == 2


@pytest.mark.parametrize(
    "rejection",
    [
        "unlinked",
        "external",
        "deleted",
        "is_bot",
        "is_restricted",
        "is_ultra_restricted",
        "is_app_user",
    ],
)
async def test_dm_rejects_unauthorized_recipients_before_opening_conversation(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch, rejection: str
) -> None:
    from openswe.slack.tools import channels

    monkeypatch.setattr(
        User,
        "login_for_slack",
        AsyncMock(return_value=None if rejection == "unlinked" else "octocat"),
    )
    monkeypatch.setattr(channels, "slack_identity", AsyncMock(return_value={"team_id": "T123"}))
    user = {
        "id": "U123",
        "team_id": "T123",
        "deleted": False,
        "is_bot": False,
        "is_restricted": False,
        "is_ultra_restricted": False,
        "is_app_user": False,
    }
    if rejection == "external":
        user["team_id"] = "T456"
    elif rejection != "unlinked":
        user[rejection] = True
    slack_api.respond({"ok": True, "user": user})
    result = await tools.slack_send_dm("U123", "hello")
    assert result["success"] is False
    assert not any(
        method in ("conversations.open", "chat.postMessage") for method, _ in slack_api.calls
    )


async def test_dm_posts_to_authorized_recipient_and_records_concierge_context(
    slack_api: SlackAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openswe.slack import dm
    from openswe.slack.tools import channels

    monkeypatch.setattr(User, "login_for_slack", AsyncMock(return_value="octocat"))
    monkeypatch.setattr(User, "concierge_mode_for_slack", AsyncMock(return_value=True))
    monkeypatch.setattr(channels, "slack_identity", AsyncMock(return_value={"team_id": "T123"}))
    record = AsyncMock()
    monkeypatch.setattr(dm, "_record_in_concierge_thread", record)
    slack_api.respond(
        {
            "ok": True,
            "user": {
                "id": "U123",
                "team_id": "T123",
                "deleted": False,
                "is_bot": False,
                "is_restricted": False,
                "is_ultra_restricted": False,
            },
        }
    )
    slack_api.respond({"ok": True, "channel": {"id": "D123"}})
    slack_api.respond({"ok": True, "ts": "1.0"})
    assert await tools.slack_send_dm("U123", "hello") == {
        "success": True,
        "channel_id": "D123",
        "message_ts": "1.0",
    }
    record.assert_awaited_once_with("D123", "hello")
