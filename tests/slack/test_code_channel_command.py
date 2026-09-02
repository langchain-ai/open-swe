from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import spawn
from agent.webhooks import slack as slack_webhook

COMMAND = {
    "channel_id": "C-origin",
    "user_id": "U1",
    "text": "fix the flaky login test",
    "response_url": "https://hooks.slack.com/commands/T1/1/abc",
    "team_id": "T1",
    "repo": {"owner": "acme", "name": "billing"},
}


@pytest.fixture
def command(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {
        "open_code_channel": AsyncMock(
            return_value=SimpleNamespace(
                channel_id="C-code",
                session=SimpleNamespace(thread_id="thread-code", run_id="run-1"),
                invited=["U1"],
                warnings=[],
            )
        ),
    }
    for name, mock in calls.items():
        monkeypatch.setattr(slack_webhook, name, mock)
    shared = {
        "respond_to_slack_command": AsyncMock(return_value=True),
        "login_for_slack_id": AsyncMock(return_value="octocat"),
        "get_slack_user_info": AsyncMock(
            return_value={"profile": {"display_name": "Ramon"}, "name": "ramon"}
        ),
    }
    for name, mock in shared.items():
        monkeypatch.setattr(slack_webhook.common, name, mock)
    return {**calls, **shared}


async def test_a_command_opens_a_channel_and_says_where(command: dict[str, Any]) -> None:
    await slack_webhook.process_code_channel_command(dict(COMMAND))

    opened = command["open_code_channel"].await_args.kwargs
    assert opened["title"] == "fix the flaky login test"
    assert opened["repo"] == {"owner": "acme", "name": "billing"}
    assert opened["team_id"] == "T1"
    command["respond_to_slack_command"].assert_awaited_once_with(
        COMMAND["response_url"], "Working on it in <#C-code>."
    )


async def test_the_command_leaves_nothing_behind_in_the_channel_it_came_from(
    command: dict[str, Any],
) -> None:
    """No message means no origin pair, so the caller is invited directly."""
    await slack_webhook.process_code_channel_command(dict(COMMAND))

    opened = command["open_code_channel"].await_args.kwargs
    assert "origin_channel_id" not in opened
    assert "origin_message_ts" not in opened
    assert opened["source_context"]["opened_by_command"] == {
        "channel_id": "C-origin",
        "user_id": "U1",
    }
    # Nobody is in a channel opened this way unless it puts them there.
    assert opened["invite"] == ["U1"]


async def test_the_new_session_is_told_the_task_and_that_it_starts_clean(
    command: dict[str, Any],
) -> None:
    await slack_webhook.process_code_channel_command(dict(COMMAND))

    content = command["open_code_channel"].await_args.kwargs["content"]
    assert "fix the flaky login test" in content
    assert "acme/billing" in content
    assert "fresh sandbox" in content
    assert "Asked for by Ramon" in content
    assert "<#C-origin>" in content


async def test_the_caller_inherits_their_github_identity(command: dict[str, Any]) -> None:
    await slack_webhook.process_code_channel_command(dict(COMMAND))

    origin = command["open_code_channel"].await_args.kwargs["origin"]
    assert origin.configurable["github_login"] == "octocat"
    assert origin.slack_thread.triggering_user_id == "U1"
    assert origin.slack_thread.triggering_user_name == "Ramon"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("fix the flaky login test", "fix the flaky login test"),
        ("Migrate the cron. Then delete the old one.", "Migrate the cron"),
        ("first line\nsecond line", "first line"),
        ("   ", "Open SWE task"),
        ("x" * 200, "x" * 120),
    ],
    ids=["one line", "first sentence", "first paragraph", "nothing", "too long"],
)
def test_the_channel_is_named_from_the_prompt(prompt: str, expected: str) -> None:
    """A provisional name; the session renames the channel once it has a title."""
    assert slack_webhook._command_title(prompt) == expected


async def test_a_channel_that_cannot_be_opened_is_reported_to_the_caller(
    command: dict[str, Any],
) -> None:
    command["open_code_channel"].side_effect = spawn.CodeChannelError("feature_disabled")

    await slack_webhook.process_code_channel_command(dict(COMMAND))

    command["respond_to_slack_command"].assert_awaited_once_with(
        COMMAND["response_url"], "Could not open a code channel: feature_disabled"
    )


async def test_an_unexpected_failure_still_answers_the_caller(command: dict[str, Any]) -> None:
    command["open_code_channel"].side_effect = RuntimeError("boom")

    await slack_webhook.process_code_channel_command(dict(COMMAND))

    text = command["respond_to_slack_command"].await_args.args[1]
    assert "Could not open a code channel" in text


async def test_the_command_invites_anyone_it_names(command: dict[str, Any]) -> None:
    await slack_webhook.process_code_channel_command(
        {**COMMAND, "text": "pair with <@U2> and <@U3> on the login test"}
    )

    assert command["open_code_channel"].await_args.kwargs["invite"] == ["U1", "U2", "U3"]
