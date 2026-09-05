"""A Slack turn with no resolvable repository gets a reply, not a silent 400."""

from unittest.mock import AsyncMock

import pytest

from agent.slack import routes as slack_routes
from agent.webhooks import common as webhook_common


async def test_missing_repository_is_explained_in_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        webhook_common,
        "get_slack_repo_config",
        AsyncMock(side_effect=webhook_common.SlackRepositoryNotConfigured()),
    )
    reply = AsyncMock(return_value=True)
    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", reply)

    resolved = await slack_routes._repo_config_or_reply("C1", "123.45", slack_user_id="U1")

    assert resolved is None
    reply.assert_awaited_once()
    channel_id, thread_ts, text = reply.await_args.args
    assert (channel_id, thread_ts) == ("C1", "123.45")
    assert "Team settings" in text and "repo:owner/name" in text
    assert "Sign in with Slack" not in text


def test_reply_points_at_sign_in_with_slack_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.slack import oauth as slack_oauth

    monkeypatch.setattr(slack_oauth, "SLACK_CLIENT_ID", "123.456")
    monkeypatch.setattr(slack_oauth, "SLACK_CLIENT_SECRET", "secret")

    text = webhook_common.no_repository_slack_reply()

    assert text.startswith(webhook_common.NO_REPOSITORY_SLACK_REPLY)
    assert "My settings → Sign in with Slack" in text


async def test_resolved_repository_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        webhook_common,
        "get_slack_repo_config",
        AsyncMock(return_value={"owner": "acme", "name": "widgets"}),
    )
    reply = AsyncMock()
    monkeypatch.setattr(webhook_common, "post_slack_thread_reply", reply)

    resolved = await slack_routes._repo_config_or_reply("C1", "123.45")

    assert resolved == {"owner": "acme", "name": "widgets"}
    reply.assert_not_awaited()


def test_missing_repository_is_still_a_400_for_api_callers() -> None:
    exc = webhook_common.SlackRepositoryNotConfigured()

    assert exc.status_code == 400
    assert exc.detail == "no default repository configured"
