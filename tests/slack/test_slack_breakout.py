from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.slack import breakout
from agent.slack.channels import SlackChannel
from agent.slack.request import SlackRequest

Command = breakout.BreakoutCommand


def _channel(channel_id: str, *, private: bool) -> SlackChannel | None:
    return SlackChannel.from_payload(
        {
            "id": channel_id,
            "name": channel_id.lower(),
            "is_channel": True,
            "is_private": private,
            "is_ext_shared": False,
            "is_pending_ext_shared": False,
        }
    )


@pytest.fixture(autouse=True)
def public_channels(monkeypatch):
    load = AsyncMock(side_effect=lambda channel_id, **_: _channel(channel_id, private=False))
    monkeypatch.setattr(SlackChannel, "load", load)
    return load


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<@U0BOT> /breakout", Command("")),
        ("<@U0BOT>   /breakout  ", Command("")),
        (
            "<@U0BOT> /breakout fix the flaky test\nand add a regression",
            Command("fix the flaky test\nand add a regression"),
        ),
        ("<@U0BOT> /BREAKOUT do it", Command("do it")),
        ("<@U0BOT> /breakout <#C2|eng>", Command("", "<#C2|eng>", "C2")),
        (
            "<@U0BOT> /breakout <#C2|eng> fix it\nnow",
            Command("fix it\nnow", "<#C2|eng>", "C2"),
        ),
        ("<@U0BOT> /breakout <#G9>  go", Command("go", "<#G9>", "G9")),
        ("<@U0BOT> /breakout #nope fix it", Command("fix it", "#nope", "")),
        ("<@U0BOT> /breakout fix #1", Command("fix #1")),
        ("<@U0BOT> please /breakout this", None),
        ("<@U0BOT> /breakouts", None),
        ("<@U0BOT> break out", None),
    ],
)
def test_parse_breakout_command(text, expected):
    assert Command.parse(text, "U0BOT") == expected


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
        reactions=AsyncMock(),
        ephemeral=AsyncMock(),
        source_line=AsyncMock(return_value="<https://slack/p105|(source)>"),
    )
    monkeypatch.setattr(breakout, "source_thread_line", posted.source_line)
    monkeypatch.setattr(breakout, "mark_broken_out", posted.reactions)
    monkeypatch.setattr(breakout, "post_slack_ephemeral_message", posted.ephemeral)
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

    await breakout.process_slack_breakout(_request(), Command("fix it"), None)

    assert root.await_args.args[1] == (
        "`/breakout`: fix it · <https://slack/p105|(source)> · <@U_ALICE>"
    )
    posted.source_line.assert_awaited_once_with("C1", "105.0")
    posted.reactions.assert_awaited_once_with("C1", "100.0", "105.0", "C1", "200.0")
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

    await breakout.process_slack_breakout(_request(), Command(""), None)

    _, thread_id, _, channel, message = move.await_args.args
    assert (thread_id, channel) == ("old-thread", "C1")
    assert message == "`/breakout`: Flaky test · <https://slack/p105|(source)> · <@U_ALICE>"
    posted.source_line.assert_awaited_once_with("C1", "105.0")
    posted.reactions.assert_awaited_once_with("C1", "100.0", "105.0", "C1", "200.0")
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

    await breakout.process_slack_breakout(_request(), Command(""), None)

    move.assert_not_awaited()
    posted.reactions.assert_not_awaited()
    posted.ephemeral.assert_awaited_once()


@pytest.mark.asyncio
async def test_breakout_to_channel_starts_there_with_source_transcript(monkeypatch):
    posted = _patch_slack(monkeypatch)
    root = AsyncMock(return_value=("200.0", None))
    monkeypatch.setattr(breakout, "post_slack_top_level_message_with_ts", root)
    monkeypatch.setattr(breakout, "langgraph_client", lambda: object())
    resolve = AsyncMock(return_value="new-thread")
    monkeypatch.setattr(breakout.common, "resolve_slack_thread_id", resolve)
    target_repo = object()
    repo_config = AsyncMock(return_value=target_repo)
    monkeypatch.setattr(breakout.common, "get_slack_repo_config", repo_config)
    mention = AsyncMock()
    monkeypatch.setattr(breakout.service, "process_slack_mention", mention)

    await breakout.process_slack_breakout(
        _request(), Command("fix it", "<#C2|eng>", "C2"), object()
    )

    assert root.await_args.args[0] == "C2"
    resolve.assert_awaited_once_with(resolve.await_args.args[0], "C2", "200.0")
    repo_config.assert_awaited_once_with(
        "C2", "200.0", slack_user_id="U_ALICE", thread_id="old-thread"
    )
    assert mention.await_args.args[1] is target_repo
    posted.reactions.assert_awaited_once_with("C1", "100.0", "105.0", "C2", "200.0")
    sent = mention.await_args.args[0]
    assert (sent.channel_id, sent.thread_ts, sent.context_channel_id, sent.context_thread_ts) == (
        "C2",
        "200.0",
        "C1",
        "100.0",
    )
    posted.ephemeral.assert_not_awaited()


@pytest.mark.asyncio
async def test_breakout_to_unknown_channel_errors_in_thread(monkeypatch):
    posted = _patch_slack(monkeypatch)
    root = AsyncMock()
    monkeypatch.setattr(breakout, "post_slack_top_level_message_with_ts", root)

    await breakout.process_slack_breakout(_request(), Command("fix it", "#nope", ""), None)

    root.assert_not_awaited()
    posted.reactions.assert_not_awaited()
    channel, user, text, thread_ts = posted.ephemeral.await_args.args
    assert (channel, user, thread_ts) == ("C1", "U_ALICE", "100.0")
    assert "`#nope`" in text


@pytest.mark.asyncio
async def test_breakout_to_channel_without_bot_errors_in_thread(monkeypatch):
    posted = _patch_slack(monkeypatch)
    monkeypatch.setattr(
        breakout,
        "post_slack_top_level_message_with_ts",
        AsyncMock(return_value=(None, "not_in_channel")),
    )

    await breakout.process_slack_breakout(_request(), Command("fix it", "<#C2>", "C2"), None)

    posted.reactions.assert_not_awaited()
    channel, _, text, thread_ts = posted.ephemeral.await_args.args
    assert (channel, thread_ts) == ("C1", "100.0")
    assert "<#C2>" in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [Command("fix it", "<#G2|secret>", "G2"), Command("fix it"), Command("")],
)
async def test_breakout_never_goes_to_a_private_channel(monkeypatch, public_channels, command):
    posted = _patch_slack(monkeypatch)
    public_channels.side_effect = lambda channel_id, **_: _channel(channel_id, private=True)
    root = AsyncMock()
    monkeypatch.setattr(breakout, "post_slack_top_level_message_with_ts", root)
    move = AsyncMock()
    monkeypatch.setattr(breakout, "move_slack_thread", move)

    await breakout.process_slack_breakout(_request(), command, None)

    root.assert_not_awaited()
    move.assert_not_awaited()
    posted.reactions.assert_not_awaited()
    assert posted.ephemeral.await_args.args[3] == "100.0"


@pytest.mark.asyncio
async def test_breakout_refuses_a_channel_it_cannot_look_up(monkeypatch, public_channels):
    posted = _patch_slack(monkeypatch)
    public_channels.side_effect = None
    public_channels.return_value = None
    root = AsyncMock()
    monkeypatch.setattr(breakout, "post_slack_top_level_message_with_ts", root)

    await breakout.process_slack_breakout(_request(), Command("fix it", "<#C2>", "C2"), None)

    root.assert_not_awaited()
    posted.ephemeral.assert_awaited_once()


@pytest.mark.asyncio
async def test_breakout_never_leaves_a_private_channel(monkeypatch, public_channels):
    posted = _patch_slack(monkeypatch)
    public_channels.side_effect = lambda channel_id, **_: _channel(
        channel_id, private=channel_id == "C1"
    )
    root = AsyncMock()
    monkeypatch.setattr(breakout, "post_slack_top_level_message_with_ts", root)

    await breakout.process_slack_breakout(_request(), Command("fix it", "<#C2>", "C2"), None)

    root.assert_not_awaited()
    posted.reactions.assert_not_awaited()
    assert "<#C1>" in posted.ephemeral.await_args.args[2]
