from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from openswe.slack import breakout
from openswe.slack.channels import SlackChannel
from openswe.slack.parsed_message import ParsedSlackMessage
from openswe.slack.request import SlackRequest
from openswe.users import User
from openswe.workspaces import routing

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
    monkeypatch.setattr(
        breakout.common, "get_thread_workspace", AsyncMock(return_value="engineering")
    )
    monkeypatch.setattr(breakout.common, "thread_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(User, "login_for_slack", AsyncMock(return_value="alice"))
    return load


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<@U0BOT> /breakout fix it", Command("fix it")),
        (
            "details <@U0BOT> /breakout <#C2|target> fix it",
            Command("fix it", "<#C2|target>", "C2", "details"),
        ),
        ("details <@U0BOT> /breakout", Command("", prior_text="details")),
        (
            "<@U0BOT> /workspace:infra /breakout fix it",
            Command("fix it", options="/workspace:infra", workspace="infra"),
        ),
        ("<@U0BOT> /breakout:web <#C2> why", Command("<#C2> why")),
    ],
)
def test_breakout_command_from_message(text, expected):
    assert Command.from_message(ParsedSlackMessage.parse(text, "U0BOT")) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("explicit", "expected", "email_only"),
    [
        ("", "C1", False),
        ("C3", "C3", False),
        ("", "C1", True),
    ],
)
async def test_breakout_with_text_starts_new_thread_with_old_transcript(
    monkeypatch, explicit, expected, email_only
):
    posted = _patch_slack(monkeypatch)
    root = AsyncMock(return_value="200.0")
    monkeypatch.setattr(breakout, "post_slack_top_level_message_with_ts", root)
    monkeypatch.setattr(breakout, "langgraph_client", lambda: object())
    monkeypatch.setattr(
        breakout,
        "dashboard_thread_url",
        lambda thread_id: f"https://dashboard.example/agents/{thread_id}",
    )
    update = AsyncMock(return_value=None)
    monkeypatch.setattr(breakout, "update_slack_message", update)
    monkeypatch.setattr(
        breakout.common, "resolve_slack_thread_id", AsyncMock(return_value="new-thread")
    )
    mention = AsyncMock()
    monkeypatch.setattr(breakout.service, "process_slack_mention", mention)

    request = _request()
    command = Command("fix it", channel_id=explicit, options="/workspace:other", workspace="other")
    if email_only:
        request = request.model_copy(update={"thread_id": None})
        command = Command("fix it", channel_id=explicit)
        monkeypatch.setattr(User, "login_for_slack", AsyncMock(return_value=None))
        monkeypatch.setattr(
            breakout.common,
            "get_slack_user_info",
            AsyncMock(return_value={"profile": {"email": "alice@example.com"}}),
        )
        monkeypatch.setattr(
            User,
            "login_for_email",
            AsyncMock(side_effect=lambda email: "alice" if email == "alice@example.com" else None),
        )
        monkeypatch.setattr(
            routing.WORKSPACES, "owner_of_slack_channel", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            routing.WORKSPACES,
            "slug_exists",
            AsyncMock(side_effect=lambda slug: slug == "engineering"),
        )
        monkeypatch.setattr(
            routing,
            "get_user_preferences",
            AsyncMock(
                side_effect=lambda login: (
                    {"default_workspace": "engineering"} if login == "alice" else {}
                )
            ),
        )
    monkeypatch.setattr(breakout.common, "get_slack_repo_config", AsyncMock(return_value=None))
    await breakout.process_slack_breakout(request, command, None)

    assert mention.await_args is not None
    assert mention.await_args.kwargs["inherited_workspace"] == (None if explicit else "engineering")
    assert root.await_args is not None
    assert root.await_args.args[0] == expected
    assert root.await_args.args[1] == (
        "`/breakout`: fix it · <https://slack/p105|(source)> · <@U_ALICE>"
    )
    update.assert_awaited_once_with(
        expected,
        "200.0",
        "`/breakout`: fix it · <https://slack/p105|(source)> · <@U_ALICE> "
        "<https://dashboard.example/agents/new-thread|Open in Web>",
        blocks=ANY,
        unfurl_links=False,
        unfurl_media=False,
    )
    posted.source_line.assert_awaited_once_with("C1", "105.0")
    posted.reactions.assert_awaited_once_with("C1", "100.0", "105.0", expected, "200.0")
    sent = mention.await_args.args[0]
    assert (sent.thread_ts, sent.thread_id, sent.text, sent.context_thread_ts) == (
        "200.0",
        "new-thread",
        command.thread_text,
        "100.0",
    )
    assert sent.breakout_root_suffix == " · <https://slack/p105|(source)> · <@U_ALICE>"
    posted.ephemeral.assert_not_awaited()


@pytest.mark.asyncio
async def test_breakout_after_mention_preserves_preceding_text_as_prior_message(monkeypatch):
    posted = _patch_slack(monkeypatch)
    root = AsyncMock(return_value="200.0")
    monkeypatch.setattr(breakout, "post_slack_top_level_message_with_ts", root)
    monkeypatch.setattr(breakout, "langgraph_client", lambda: object())
    monkeypatch.setattr(
        breakout.common, "resolve_slack_thread_id", AsyncMock(return_value="new-thread")
    )
    mention = AsyncMock()
    monkeypatch.setattr(breakout.service, "process_slack_mention", mention)
    text = "Context for this task.\nMore details <@U0BOT> /breakout fix it"
    command = Command.from_message(ParsedSlackMessage.parse(text, "U0BOT"))

    assert command == Command("fix it", prior_text="Context for this task.\nMore details")
    await breakout.process_slack_breakout(
        _request().model_copy(update={"text": text}), command, None
    )

    assert root.await_args.args[1].startswith("`/breakout`: fix it · ")
    sent = mention.await_args.args[0]
    assert sent.text == "fix it"
    assert sent.prior_message_text == "Context for this task.\nMore details"
    assert sent.context_thread_ts == "100.0"
    posted.reactions.assert_awaited_once()


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
