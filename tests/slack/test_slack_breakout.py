import json
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from openswe.slack import breakout, breakout_destination, breakout_links
from openswe.slack.breakout_choice import BreakoutChoice
from openswe.slack.channels import SlackChannel
from openswe.slack.kitchen_links import KitchenLink
from openswe.slack.parsed_message import ParsedSlackMessage
from openswe.slack.payloads import SlackButtonValue, SlackInteraction
from openswe.slack.request import SlackRequest
from openswe.users import User
from openswe.workspaces import routing

Command = breakout.BreakoutCommand
_KITCHEN_FOR = KitchenLink.__dict__["kitchen_for"]


def _channel(channel_id: str, *, private: bool) -> SlackChannel | None:
    return SlackChannel.from_payload(
        {
            "id": channel_id,
            "name": channel_id.lower(),
            "is_channel": True,
            "num_members": 10,
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
    monkeypatch.setattr(breakout_destination, "is_kitchen_channel", AsyncMock(return_value=False))
    monkeypatch.setattr(KitchenLink, "kitchen_for", AsyncMock(return_value=None))
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
        notifications=AsyncMock(),
        ephemeral=AsyncMock(),
        source_line=AsyncMock(return_value="<https://slack/p105|(source)>"),
    )
    monkeypatch.setattr(breakout, "source_thread_line", posted.source_line)
    monkeypatch.setattr(breakout, "mark_broken_out", posted.notifications)
    monkeypatch.setattr(breakout, "post_slack_ephemeral_message", posted.ephemeral)
    return posted


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<@U0BOT> /breakout fix it", Command("fix it")),
        ("<@U0BOT> /breakout in <#C2> fix it", Command("fix it", "<#C2>", "C2")),
        ("<@U0BOT> /breakout in <#C2>", Command("", "<#C2>", "C2")),
        ("<@U0BOT> /breakout in #target fix it", Command("fix it", "#target")),
        ("<@U0BOT> /breakout in the parser", Command("in the parser")),
        (
            "details <@U0BOT> /breakout <#C2|target> fix it",
            Command("fix it", "<#C2|target>", "C2", "details"),
        ),
        ("details <@U0BOT> /breakout", Command("", prior_text="details")),
        (
            "<@U0BOT> /WORKSPACE:Infra /breakout fix it",
            Command("fix it", options="/workspace:infra", workspace="infra"),
        ),
        ("<@U0BOT> /breakout:web <#C2> why", Command("<#C2> why")),
        (
            "<@U0BOT> /breakout fix workspace:infra bug",
            Command("fix workspace:infra bug", workspace="infra"),
        ),
    ],
)
def test_breakout_command_from_message(text, expected):
    assert Command.from_message(ParsedSlackMessage.parse(text, "U0BOT")) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("explicit", "expected", "email_only", "private_channel"),
    [
        ("", "C1", False, False),
        ("C3", "C3", False, False),
        ("", "C1", True, False),
        ("", "C1", False, True),
        ("C1", "C1", False, True),
    ],
)
async def test_breakout_with_text_starts_new_thread_with_old_transcript(
    monkeypatch, explicit, expected, email_only, private_channel, public_channels
):
    public_channels.side_effect = lambda channel_id, **_: _channel(
        channel_id, private=private_channel
    )
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
    assert mention.await_args.kwargs["inherited_workspace"] == (
        "engineering" if expected == "C1" else None
    )
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
    assert root.await_args.kwargs["blocks"] == [
        {"type": "section", "text": {"type": "mrkdwn", "text": root.await_args.args[1]}}
    ]
    assert update.await_args.kwargs["blocks"] == [
        {"type": "section", "text": {"type": "mrkdwn", "text": root.await_args.args[1]}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "<https://dashboard.example/agents/new-thread|Open in Web>",
                }
            ],
        },
    ]
    posted.source_line.assert_awaited_once_with("C1", "105.0")
    posted.notifications.assert_awaited_once_with("C1", "100.0", expected, "200.0")
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
@pytest.mark.parametrize(
    ("count", "kitchen", "choices"),
    [(50, None, ["C1"]), (None, None, ["C1"]), (3, "CK", ["CK", "C1"])],
)
async def test_default_breakout_offers_destination_buttons(
    monkeypatch, public_channels, count, kitchen, choices
):
    public_channels.side_effect = lambda channel_id, **_: SlackChannel.from_payload(
        {"id": channel_id, "is_channel": True, "num_members": count, "is_private": False}
    )
    monkeypatch.setattr(KitchenLink, "kitchen_for", AsyncMock(return_value=kitchen))
    posted = _patch_slack(monkeypatch)
    offer = AsyncMock(return_value=True)
    monkeypatch.setattr(BreakoutChoice, "offer", offer)
    start = AsyncMock()
    monkeypatch.setattr(breakout, "_start", start)

    await breakout.process_slack_breakout(_request(), Command("fix it"), None)

    start.assert_not_awaited()
    posted.ephemeral.assert_not_awaited()
    assert offer.await_args.args == ("C1", "100.0", "U_ALICE", choices)
    stored = offer.await_args.kwargs["breakout_command"]
    assert stored["command"]["instruction"] == "fix it"

    await breakout.process_slack_breakout(_request(), Command("fix it", channel_id="C1"), None)

    start.assert_awaited_once()


@pytest.mark.asyncio
async def test_breakout_choice_runs_only_for_its_requester(monkeypatch):
    request = _request()
    choice = BreakoutChoice(
        channel_id="C1",
        thread_ts="100.0",
        destination_channel_ids=["CK", "C1"],
        breakout_command={
            "request": request.model_dump(mode="json"),
            "command": {"instruction": "fix it"},
        },
        created_by=uuid4(),
    )
    monkeypatch.setattr(BreakoutChoice, "pending", AsyncMock(return_value=choice))
    claim = AsyncMock(return_value=True)
    monkeypatch.setattr(BreakoutChoice, "claim", claim)
    monkeypatch.setattr(breakout, "respond_to_slack_interaction", AsyncMock())
    monkeypatch.setattr(breakout.common, "get_slack_repo_config", AsyncMock(return_value=None))
    requester = SimpleNamespace(id=choice.created_by)
    identity = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(User, "for_identity", identity)
    interaction = SlackInteraction.model_validate(
        {"user": {"id": "U_ALICE"}, "channel": {"id": "C1"}, "response_url": "https://hooks"}
    )
    value = SlackButtonValue(type="breakout_destination", subject=str(choice.id), action="CK")
    tasks = BackgroundTasks()

    await breakout.handle_destination_button(interaction, value, tasks)

    claim.assert_not_awaited()
    assert not tasks.tasks

    identity.return_value = requester
    await breakout.handle_destination_button(interaction, value, tasks)

    claim.assert_awaited_once()
    (task,) = tasks.tasks
    assert task.func is breakout.process_slack_breakout
    assert task.args[1] == Command("fix it", "<#CK>", "CK")


@pytest.mark.asyncio
async def test_breakout_may_go_to_a_private_kitchen_channel(monkeypatch, public_channels):
    public_channels.side_effect = lambda channel_id, **_: _channel(
        channel_id, private=channel_id == "CK"
    )
    destination = breakout_destination.BreakoutDestination("CK", "engineering")

    assert await destination.channel_error("C1") is not None

    monkeypatch.setattr(
        breakout_destination,
        "is_kitchen_channel",
        AsyncMock(side_effect=lambda channel_id: channel_id == "CK"),
    )
    assert await destination.channel_error("C1") is None


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
    posted.notifications.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [True, False])
async def test_web_breakout_notifies_source_thread_only_after_starting(monkeypatch, started):
    posted = _patch_slack(monkeypatch)
    monkeypatch.setattr(
        breakout.service, "process_slack_web_mention", AsyncMock(return_value=started)
    )
    monkeypatch.setattr(breakout, "dashboard_thread_url", lambda _: "https://dashboard/agents/new")
    reply = AsyncMock()
    monkeypatch.setattr(breakout_links, "post_slack_thread_reply", reply)

    await breakout.process_slack_web(_request(), Command("fix it"), None)

    if started:
        text = "<@U_ALICE> continued in a <https://dashboard/agents/new|private web breakout>."
        reply.assert_awaited_once_with(
            "C1",
            "100.0",
            text,
            blocks=[{"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}],
            unfurl_links=False,
            unfurl_media=False,
        )
    else:
        reply.assert_not_awaited()
    posted.ephemeral.assert_not_awaited()


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
    posted.notifications.assert_not_awaited()
    posted.ephemeral.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [Command("fix it", "<#G2|secret>", "G2"), Command("", "<#G2|secret>", "G2")],
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
    posted.notifications.assert_not_awaited()
    assert posted.ephemeral.await_args.args[3] == "100.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["", "C1", "C2"])
async def test_breakout_refuses_a_channel_it_cannot_look_up(monkeypatch, public_channels, target):
    posted = _patch_slack(monkeypatch)
    public_channels.side_effect = None
    public_channels.return_value = None
    root = AsyncMock()
    monkeypatch.setattr(breakout, "post_slack_top_level_message_with_ts", root)

    await breakout.process_slack_breakout(_request(), Command("fix it", channel_id=target), None)

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
    posted.notifications.assert_not_awaited()
    assert "public source channel and destination" in posted.ephemeral.await_args.args[2]


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_kitchen_link_matches_by_name_and_is_cached(monkeypatch):
    from openswe.workspaces.store import WORKSPACES, WorkspaceCreate

    await SlackChannel.save_all(
        {"id": "C0TEAM", "name": "team-open-swe"},
        {"id": "C0KITCHEN", "name": "team-openswe-kitchen"},
        {"id": "C0OTHER", "name": "other"},
    )
    await WORKSPACES.create(
        WorkspaceCreate(
            name="Core",
            repos=["acme/api"],
            slack_channel_ids=["C0KITCHEN"],
            kitchen_channel_ids=["C0KITCHEN"],
        ),
        "alice",
    )
    monkeypatch.setattr(KitchenLink, "kitchen_for", _KITCHEN_FOR)
    name_for = AsyncMock(side_effect=SlackChannel.name_for)
    monkeypatch.setattr(SlackChannel, "name_for", name_for)

    assert await KitchenLink.kitchen_for("C0TEAM") == "C0KITCHEN"
    assert await KitchenLink.kitchen_for("C0TEAM") == "C0KITCHEN"
    assert await KitchenLink.kitchen_for("C0OTHER") is None
    assert await KitchenLink.kitchen_for("C0OTHER") is None
    assert name_for.await_count == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_breakout_choice_is_claimed_once(monkeypatch):
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    requester = await User.sign_in("github", "1", login="alice")
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=requester))
    ephemeral = AsyncMock(return_value=True)
    monkeypatch.setattr("openswe.slack.breakout_choice.post_slack_ephemeral_message", ephemeral)
    monkeypatch.setattr(SlackChannel, "name_for", AsyncMock(return_value="team-kitchen"))

    assert await BreakoutChoice.offer("C1", "100.0", "U_ALICE", ["CK", "C1"])

    value = json.loads(ephemeral.await_args.kwargs["blocks"][1]["elements"][0]["value"])
    choice = await BreakoutChoice.pending(value["subject"])
    assert choice is not None and choice.created_by == requester.id
    assert await choice.claim()
    assert not await choice.claim()
    assert await BreakoutChoice.pending(value["subject"]) is None
