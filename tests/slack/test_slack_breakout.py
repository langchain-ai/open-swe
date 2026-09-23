from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.slack import breakout
from agent.slack.request import SlackRequest


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<@U0BOT> /breakout", ""),
        ("<@U0BOT>   /breakout  ", ""),
        (
            "<@U0BOT> /breakout fix the flaky test\nand add a regression",
            "fix the flaky test\nand add a regression",
        ),
        ("<@U0BOT> /BREAKOUT do it", "do it"),
        ("<@U0BOT> please /breakout this", None),
        ("<@U0BOT> /breakouts", None),
        ("<@U0BOT> break out", None),
    ],
)
def test_parse_breakout_command(text, expected):
    assert breakout.parse_breakout_command(text, "U0BOT") == expected


def _request() -> SlackRequest:
    return SlackRequest(
        channel_id="C1",
        thread_ts="100.0",
        event_ts="105.0",
        original_message_ts="105.0",
        user_id="U_ALICE",
        text="<@U0BOT> /breakout fix it",
        bot_user_id="U0BOT",
        thread_id="old-thread",
    )


def _patch_slack(monkeypatch) -> SimpleNamespace:
    posted = SimpleNamespace(
        replies=AsyncMock(),
        ephemeral=AsyncMock(),
    )
    monkeypatch.setattr(
        breakout,
        "fetch_slack_thread_messages",
        AsyncMock(
            return_value=[
                {"ts": "100.0", "user": "U_ALICE", "text": "flaky test"},
                {"ts": "101.0", "user": "U0BOT", "bot_id": "B1", "text": "on it"},
                {"ts": "102.0", "user": "U_BOB", "text": "same here"},
                {"ts": "105.0", "user": "U_ALICE", "text": "<@U0BOT> /breakout fix it"},
            ]
        ),
    )
    monkeypatch.setattr(
        breakout,
        "source_thread_line",
        AsyncMock(
            return_value=":arrow_right_hook: Broken out from <https://slack/p100|this thread>"
        ),
    )
    monkeypatch.setattr(breakout, "post_breakout_link", posted.replies)
    monkeypatch.setattr(breakout, "post_slack_ephemeral_reply", posted.ephemeral)
    return posted


@pytest.mark.asyncio
async def test_breakout_with_text_starts_new_thread_with_old_transcript(monkeypatch):
    posted = _patch_slack(monkeypatch)
    root = AsyncMock(return_value=("200.0", None))
    monkeypatch.setattr(breakout, "post_slack_top_level_message_with_ts", root)
    monkeypatch.setattr(breakout, "langgraph_client", lambda: object())
    monkeypatch.setattr(
        breakout.common, "resolve_slack_thread_id", AsyncMock(return_value="new-thread")
    )
    mention = AsyncMock()
    monkeypatch.setattr(breakout.service, "process_slack_mention", mention)

    await breakout.process_slack_breakout(_request(), "fix it", None)

    assert root.await_args.args[1] == (
        "*Open SWE breakout thread:* fix it\n"
        ":arrow_right_hook: Broken out from <https://slack/p100|this thread>\n"
        "<@U_ALICE> <@U_BOB>"
    )
    posted.replies.assert_awaited_once_with("C1", "100.0", "200.0")
    sent = mention.await_args.args[0]
    assert (sent.thread_ts, sent.thread_id, sent.text, sent.context_thread_ts) == (
        "200.0",
        "new-thread",
        "fix it",
        "100.0",
    )
    posted.ephemeral.assert_not_awaited()


@pytest.mark.asyncio
async def test_bare_breakout_moves_the_existing_thread(monkeypatch):
    posted = _patch_slack(monkeypatch)
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": {"title": "Flaky test"}}))
    )
    monkeypatch.setattr(breakout, "langgraph_client", lambda: client)
    monkeypatch.setattr(breakout.common, "thread_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(
        breakout,
        "get_active_slack_thread",
        AsyncMock(return_value={"channel_id": "C1", "thread_ts": "100.0"}),
    )
    move = AsyncMock(return_value={"success": True, "thread_ts": "200.0"})
    monkeypatch.setattr(breakout, "move_slack_thread", move)
    mention = AsyncMock()
    monkeypatch.setattr(breakout.service, "process_slack_mention", mention)

    await breakout.process_slack_breakout(_request(), "", None)

    _, thread_id, _, channel, message = move.await_args.args
    assert (thread_id, channel) == ("old-thread", "C1")
    assert message == (
        "*Open SWE breakout thread:* Flaky test\n"
        ":arrow_right_hook: Broken out from <https://slack/p100|this thread>\n"
        "<@U_ALICE> <@U_BOB>"
    )
    posted.replies.assert_awaited_once_with("C1", "100.0", "200.0")
    mention.assert_not_awaited()


@pytest.mark.asyncio
async def test_bare_breakout_refuses_private_threads(monkeypatch):
    posted = _patch_slack(monkeypatch)
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"metadata": {"visibility": "private", "owner_login": "alice"}}
            )
        )
    )
    monkeypatch.setattr(breakout, "langgraph_client", lambda: client)
    monkeypatch.setattr(breakout.common, "thread_exists", AsyncMock(return_value=True))
    move = AsyncMock()
    monkeypatch.setattr(breakout, "move_slack_thread", move)

    await breakout.process_slack_breakout(_request(), "", None)

    move.assert_not_awaited()
    posted.replies.assert_not_awaited()
    posted.ephemeral.assert_awaited_once()
