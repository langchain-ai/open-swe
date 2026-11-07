"""What Open SWE's Slack channel does with each message in the conversations it holds.

The handler decides what a message starts from the message itself, as the
backend's ``/webhooks/slack`` decides for the conversations it holds: a mention,
a direct message, a kitchen channel's message, a follow-up in a thread only its
author talks in, a ``/btw`` question, a breakout or a move. It asks the backend
only for what needs Open SWE's records (``runtime_api.slack``): the facts the
decision needs (``slack_message_facts``), then the turn (``start_slack_turn``),
which gates it, makes a new conversation this channel's, records the thread and
signs the run. A turn's prompt is built here, from the Slack thread as the bot
token reads it (``rt.slack_prompt``); a ``/btw`` question, a breakout and a move
come back built. A mention, a direct message or an explicit request redirects
the thread's work in progress; any other follow-up waits its turn behind it. A
concierge DM's runs go on the DM's one thread, and a ``/btw`` question's or a new
breakout's on one of their own, which the backend names. When the backend starts
nothing and says why, such as a person who must sign in to Open SWE first, the
handler replies with it in the Slack thread. A code channel's messages stay with
the backend, which holds its one session.
"""

import logging
import re
from collections.abc import Mapping
from typing import Any, Final, Literal, cast

from langchain_core.messages.content import create_text_block
from managed_deepagents import (
    ChannelReplyResult,
    ChannelRun,
    ChannelRunResult,
    SlackActivityEvent,
    SlackChannelThread,
    SlackMessageEvent,
    reply,
)
from managed_deepagents._channels.slack.api import (  # noqa: PLC2701
    slack_api,
    slack_bot_token,
)
from pydantic import JsonValue
from runtime_api.slack import (
    SLACK_MESSAGE_FACTS,
    START_SLACK_TURN,
    SlackMessage,
    SlackMessageFacts,
    SlackMessageFactsRequest,
    SlackTurnDeclined,
    SlackTurnStart,
    StartSlackTurn,
)

from rt import slack_prompt
from rt.input_messages import ChannelIdentity
from rt.intake import ask_backend
from rt.slack_message import Breakout, ParsedSlackMessage, SlackAction

EXTERNAL_CHANNEL: Final = "Robin does not operate in channels with external participants."
#: The message subtypes this channel starts runs for, as Trigger Server forwards them.
_STARTED_SUBTYPES: Final = frozenset({"", "file_share"})
#: The subtypes an untagged message in a kitchen channel or a solo thread counts with.
_REQUEST_SUBTYPES: Final = frozenset({"", "file_share", "thread_broadcast"})
#: A concierge DM's one conversation, as the backend keys it.
CONCIERGE_TS: Final = "0"
_LEADING_MENTION: Final = re.compile(r"\s*<@([^>]+)>")
_REVIEW_CARD_PREFIXES: Final = ("Review requested for ", "Expedited review requested for ")

logger = logging.getLogger(__name__)

type Kind = Literal["turn", "by_the_way", "breakout", "web", "move"]

_bot: tuple[str, str] | None = None


def decline_text(declined: SlackTurnDeclined) -> str:
    """What the Slack thread hears when the backend starts nothing for a message."""
    if declined.reason == "external_channel":
        return EXTERNAL_CHANNEL
    if declined.reason == "breakout":
        return declined.message or "Could not break out this thread; try again."
    # Without a dashboard URL the text names the settings page, since the person
    # must still be told why nothing started.
    settings = (
        f"<{declined.settings_url}|your Robin settings>"
        if declined.settings_url
        else "your Robin settings"
    )
    if declined.reason == "sign_in_again":
        return (
            "🔐 Your GitHub sign-in is no longer valid, so I can't resolve your GitHub "
            f"account. Re-connect it in {settings}, then tag me again."
        )
    return (
        "👋 I couldn't resolve your GitHub account from Slack. Sign in with GitHub and "
        f"connect your Slack account in {settings}, then tag me again."
    )


async def _bot_identity() -> tuple[str, str]:
    """Open SWE's bot user and its name in Slack, as ``auth.test`` reports them; kept."""
    global _bot  # noqa: PLW0603 - an app keeps its bot user for good
    if _bot is None:
        identity = await slack_api(await slack_bot_token(), "auth.test", {})
        user_id, name = identity.get("user_id"), identity.get("user")
        _bot = (
            user_id if isinstance(user_id, str) else "",
            name if isinstance(name, str) else "",
        )
    return _bot


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


async def start_turn(
    event: SlackMessageEvent, thread: SlackChannelThread
) -> ChannelRunResult | ChannelReplyResult | None:
    """The run a message starts on its conversation's thread, the reply that says why
    it starts none, or ``None``."""
    raw = event.payload
    app_id = thread.source_thread_key.split(":")[2:3]
    bot_user_id, bot_username = await _bot_identity()
    message = SlackMessage(
        thread_id=thread.id,
        app_id=app_id[0] if app_id else "",
        team_id=event.team_id or "",
        channel_id=event.channel_id,
        channel_type=_text(raw.get("channel_type")),
        message_ts=event.message_ts,
        thread_ts=event.thread_ts,
        reply_thread_ts=_text(raw.get("thread_ts")),
        subtype=_text(raw.get("subtype")),
        user_id=event.user_id or "",
        bot_id=_text(raw.get("bot_id")),
        bot_app_id=_text(raw.get("app_id")),
        text=event.text,
        attachments=_attachments(raw.get("attachments")),
        explicit_mention=event.key == "mention",
    )
    if not message.team_id or not message.app_id:
        return None
    parsed = ParsedSlackMessage.parse(event.text, bot_user_id, bot_username)
    facts = await ask_backend(
        SLACK_MESSAGE_FACTS,
        SlackMessageFactsRequest(**message.model_dump(), workspace_tag=parsed.workspace or ""),
        msg_id=f"facts_{event.delivery_id}",
    )
    decided = _decide(message, facts, parsed, bot_user_id, is_message=_is_message(raw))
    if isinstance(decided, str):
        return reply(decided)
    if decided is None:
        return None
    kind, turn = decided
    if kind != "turn":
        start = await ask_backend(START_SLACK_TURN, turn, msg_id=f"msg_{event.delivery_id}")
        if start.declined is not None:
            return reply(decline_text(start.declined))
        return run_result(start, await _branch_seed(start, thread))
    return await _start_turn(
        event, thread, message, facts, parsed, turn, (bot_user_id, bot_username)
    )


def _attachments(value: object) -> list[dict[str, JsonValue]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _is_message(raw: Mapping[str, object]) -> bool:
    return raw.get("type") == "message"


def _decide(
    message: SlackMessage,
    facts: SlackMessageFacts,
    parsed: ParsedSlackMessage,
    bot_user_id: str,
    *,
    is_message: bool,
) -> str | tuple[Kind, StartSlackTurn] | None:
    """What a message starts, as the backend's ``/webhooks/slack`` decides it: ``None``
    for nothing, a reply's text, or the turn's kind and the call that starts it."""
    explicit = message.explicit_mention
    if facts.channel != "ok":
        # A person's mention in a channel with external participants hears why.
        refused = facts.channel == "external" and explicit and not message.subtype
        return EXTERNAL_CHANNEL if refused and message.user_id else None
    if facts.code_channel:
        return None
    bot = bool(message.bot_id)
    if bot and not (
        facts.allowed_bot
        and explicit
        and message.subtype in {"", "bot_message"}
        and message.bot_app_id != message.app_id
    ):
        return None
    if not (message.user_id or bot) or (bot_user_id and message.user_id == bot_user_id):
        return None
    dm = facts.dm
    kitchen = facts.kitchen_channel and not dm and is_message and not explicit
    leading = _LEADING_MENTION.match(message.text)
    if not dm and not bot and leading and leading[1] != bot_user_id:
        # Addressed to another person.
        return None
    direct_message = dm and bool(message.user_id)
    if not (
        explicit
        or (kitchen and message.subtype in _REQUEST_SUBTYPES)
        or (bot and facts.allowed_bot)
        or direct_message
        or facts.solo_followup
    ):
        return None
    breakout = Breakout.of(parsed)
    kind = _kind(message, facts, parsed, breakout, dm=dm, bot=bot)
    if kind is None:
        return None
    performance_command = parsed.performance_model and not facts.concierge and not bot
    turn = StartSlackTurn(
        **message.model_dump(),
        kind=kind,
        concierge=facts.concierge,
        kitchen_channel=kitchen,
        treat_all_messages_as_mentions=direct_message or kitchen or facts.solo_followup,
        performance_command=performance_command,
        workspace_tag=parsed.workspace or "",
        move_channel_id=breakout.channel_id if kind == "move" else "",
    )
    return kind, turn


def _kind(
    message: SlackMessage,
    facts: SlackMessageFacts,
    parsed: ParsedSlackMessage,
    breakout: Breakout,
    *,
    dm: bool,
    bot: bool,
) -> Kind | None:
    """The turn kind to start, or ``None`` when the backend holds the conversation."""
    started = message.subtype in _STARTED_SUBTYPES or (bot and message.subtype == "bot_message")
    command = message.explicit_mention and not (dm or bot) and started
    if command and parsed.action is SlackAction.BY_THE_WAY:
        return "by_the_way"
    if command and parsed.action is SlackAction.BREAKOUT_WEB and breakout.instruction:
        return "web"
    if command and parsed.action is SlackAction.BREAKOUT:
        if breakout.instruction or (breakout.channel and not breakout.channel_id):
            # A new thread, or one naming no channel Slack knows, which the breakout says.
            return "breakout"
        if facts.conversation == "mda" and not facts.concierge:
            return "move"
        return None
    if facts.conversation == "backend" or not started:
        return None
    if facts.conversation == "new" and parsed.action in {
        SlackAction.BREAKOUT,
        SlackAction.BREAKOUT_WEB,
    }:
        return None
    return "turn"


async def _start_turn(
    event: SlackMessageEvent,
    thread: SlackChannelThread,
    message: SlackMessage,
    facts: SlackMessageFacts,
    parsed: ParsedSlackMessage,
    turn: StartSlackTurn,
    bot: tuple[str, str],
) -> ChannelRunResult | ChannelReplyResult | None:
    """Read the Slack thread, ask the backend for the turn, and build its prompt."""
    bot_user_id, bot_username = bot
    root_ts = CONCIERGE_TS if facts.concierge else message.thread_ts
    context_ts = message.reply_thread_ts or root_ts
    history = await slack_prompt.slack_history(message.channel_id, context_ts)
    current = next((item for item in history if str(item.get("ts")) == message.message_ts), None)
    if current is None:
        history.append(
            {
                "ts": message.message_ts,
                "text": message.text,
                "user": message.user_id,
                "attachments": message.attachments,
                **(
                    {"bot_id": message.bot_id, "bot_profile": {"name": facts.bot_name}}
                    if message.bot_id
                    else {}
                ),
            }
        )
    elif message.attachments and not current.get("attachments"):
        current["attachments"] = message.attachments
    context, _ = slack_prompt.select_context_messages(
        history,
        message.message_ts,
        bot_user_id,
        bot_username,
        treat_all_messages_as_mentions=turn.treat_all_messages_as_mentions,
    )
    context_user_ids = [user for item in context if (user := _text(item.get("user")))]
    texts = [
        message.text,
        slack_prompt.format_messages_for_prompt(context + context, {}, bot_user_id, bot_username),
    ]
    names = await slack_prompt.user_names(
        [*context_user_ids, *(m for text in texts for m in slack_prompt.mentioned_user_ids(text))]
    )
    channel_names = await slack_prompt.public_channel_names(
        [m for text in texts for m in slack_prompt.mentioned_channel_ids(text)]
    )
    links_section, link_images = await slack_prompt.resolved_links(
        context, names, channel_id=message.channel_id
    )
    image_urls = slack_prompt.image_urls(context, link_images)
    performance = turn.performance_command
    # A known workspace tag is taken out of a new thread's first message.
    tagged = bool(parsed.workspace) and facts.workspace_known
    turn.clean_text = _clean_text(
        parsed, bot, names, channel_names, performance=performance, workspace=tagged
    )
    turn.context_user_ids = context_user_ids
    turn.participant_user_ids = sorted(slack_prompt.participant_ids(context))
    turn.has_images = bool(image_urls)
    # A reply under the agent's review request card is about its pull request; a
    # concierge DM's Slack thread holds no card.
    turn.review_card = (
        message.message_ts != root_ts
        and not (facts.concierge and message.reply_thread_ts)
        and any(
            slack_prompt.is_own_message(item, bot_user_id)
            and _text(item.get("text")).startswith(_REVIEW_CARD_PREFIXES)
            for item in history
        )
    )
    start = await ask_backend(START_SLACK_TURN, turn, msg_id=f"msg_{event.delivery_id}")
    if start.declined is not None:
        return reply(decline_text(start.declined))
    if start.run is None or start.prompt is None:
        return None
    prompt = start.prompt
    if not prompt.first_turn and tagged:
        # Only the opening message picks the workspace: a later tag stays in the text.
        turn.clean_text = _clean_text(
            parsed, bot, names, channel_names, performance=performance, workspace=False
        )
    if prompt.sender_name and message.user_id not in names:
        names[message.user_id] = prompt.sender_name
    if prompt.promptable_user_ids is not None:
        # A private thread's prompt quotes, links and shows only what its people wrote.
        allowed = set(prompt.promptable_user_ids)
        quoted = [item for item in context if _text(item.get("user")) in allowed]
        if len(quoted) != len(context):
            context = quoted
            links_section, link_images = await slack_prompt.resolved_links(
                context, names, channel_id=message.channel_id
            )
            image_urls = slack_prompt.image_urls(context, link_images)
    images = await slack_prompt.image_blocks(image_urls)
    # The model has what earlier runs on this thread handed it; a concierge DM's runs go
    # on another thread, whose state the handler can't read, so its context may repeat.
    state = await thread.get_state() if start.thread_id in {None, thread.id} else None
    visible, dispatched = slack_prompt.dispatched_context(state)
    described = {message.user_id} if prompt.first_turn else set(prompt.logins)
    run_input = slack_prompt.build_input(
        context,
        names,
        prompt.logins,
        person_ids_by_user_id=prompt.person_ids,
        channel_names_by_id=channel_names,
        channel=cast(ChannelIdentity, prompt.channel),
        bot_user_id=bot_user_id,
        event_ts=message.message_ts,
        source_message_ts=message.message_ts,
        trigger_user_id=message.user_id,
        request_text=turn.clean_text,
        request_blocks=[
            cast(dict[str, Any], create_text_block(turn.clean_text)),
            *cast(list[dict[str, Any]], images),
        ],
        turn_context=slack_prompt.thread_turn_context(
            request="\n\n".join(prompt.turn_sections), resolved_links=links_section
        ),
        constant_context=slack_prompt.thread_constant_context(
            review_guide=prompt.review_guide,
            concierge=facts.concierge,
            kitchen=turn.kitchen_channel,
        ),
        dispatched_timestamps=dispatched,
        run_described_person_ids={
            person for slack_id in described if (person := prompt.person_ids.get(slack_id))
        },
        visible_context_hashes=visible,
        trigger_system=slack_prompt.bot_system_identity(message.bot_id, facts.bot_name)
        if message.bot_id
        else None,
        explicit_mention=message.explicit_mention
        or slack_prompt.mentions_open_swe(message.text, bot_user_id, bot_username),
        bot_username=bot_username,
    )
    start.run.input = cast(dict[str, JsonValue], dict(run_input))
    return run_result(start)


async def start_edit(
    event: SlackActivityEvent, thread: SlackChannelThread
) -> ChannelRunResult | ChannelReplyResult | None:
    """What a person's edit of a message the conversation holds starts: the backend
    gives the agent the correction, as a note for its run in progress or as a turn.

    Only the author's edit of its text counts: an unfurl adds attachments alone, and
    a bot's edits are its own. The thread must hold the message, so an edit of one the
    agent never got starts nothing; a concierge DM's runs go on another thread, whose
    state the handler can't read, so its edits start nothing.
    """
    raw = event.payload
    edited, previous = raw.get("message"), raw.get("previous_message")
    if not (isinstance(edited, Mapping) and isinstance(previous, Mapping)):
        return None
    author = _text(edited.get("user"))
    text = _text(edited.get("text"))
    if (
        not author
        or author != _text(previous.get("user"))
        or edited.get("bot_id")
        or text == _text(previous.get("text"))
    ):
        return None
    _, held = slack_prompt.dispatched_context(await thread.get_state())
    if event.message_ts not in held:
        return None
    key = thread.source_thread_key.split(":")
    if len(key) < 4:
        return None
    turn = StartSlackTurn(
        thread_id=thread.id,
        app_id=key[2],
        team_id=key[3],
        channel_id=event.channel_id,
        channel_type=_text(raw.get("channel_type")),
        message_ts=event.message_ts,
        event_ts=_text(raw.get("event_ts")) or _text(raw.get("ts")),
        thread_ts=event.thread_ts,
        reply_thread_ts=_text(edited.get("thread_ts")),
        user_id=author,
        text=text,
        attachments=_attachments(edited.get("attachments")),
        kind="edit",
    )
    start = await ask_backend(START_SLACK_TURN, turn, msg_id=f"msg_{event.delivery_id}")
    if start.declined is not None:
        return reply(decline_text(start.declined))
    return run_result(start)


def _clean_text(
    parsed: ParsedSlackMessage,
    bot: tuple[str, str],
    names: dict[str, str],
    channel_names: dict[str, str],
    *,
    performance: bool,
    workspace: bool,
) -> str:
    """The request as the prompt has it: options taken out, mentions named."""
    bot_user_id, bot_username = bot
    text = (
        slack_prompt.replace_bot_mention_with_username(
            parsed.without(performance_model=performance, workspace=workspace),
            bot_user_id,
            bot_username,
        ).strip()
        or "(no text in mention)"
    )
    return slack_prompt.label_mentions(text, names, channel_names)


async def _branch_seed(
    start: SlackTurnStart, thread: SlackChannelThread
) -> Mapping[str, JsonValue] | None:
    """What a branch's new thread starts with: the state values of the conversation's
    thread, which the handler reads as the conversation's runs do; ``None`` for no branch,
    or a thread no run started on yet.

    The backend's prompt left out the Slack messages that thread holds. One that can't be
    read starts the branch without them, so the question is still answered.
    """
    if start.run is None or not start.branches_conversation:
        return None
    try:
        state = await thread.get_state()
    except Exception as error:  # noqa: BLE001 - the branch is still answered
        logger.warning(
            "Could not read the conversation a Slack branch starts with (%s)",
            type(error).__name__,
        )
        return None
    values = state["values"] if state is not None else None
    # Agent Server's state, as JSON.
    return cast(Mapping[str, JsonValue], values) if isinstance(values, Mapping) and values else None


def run_result(
    start: SlackTurnStart, seed: Mapping[str, JsonValue] | None = None
) -> ChannelRunResult | None:
    """The run the backend built, as the channel starts it, or ``None``.

    ``seed`` is what a branch's new thread starts with: the run's input follows its
    messages.
    """
    run = start.run
    if run is None:
        return None
    run_input: dict[str, Any] = dict(run.input)
    if seed:
        held = seed.get("messages")
        added = run_input.get("messages")
        run_input = {
            **seed,
            **run_input,
            "messages": [
                *(held if isinstance(held, list) else []),
                *(added if isinstance(added, list) else []),
            ],
        }
    started: ChannelRun = {
        "input": run_input,
        "context": run.context,
        "metadata": run.metadata,
        "multitask_strategy": "interrupt" if start.interrupts else "enqueue",
    }
    if run.webhook:
        started["webhook"] = run.webhook
    if start.thread_id is not None:
        # The agent answers with its own Slack tools, so the run posts nothing (no ``auto_post``).
        started["thread"] = {"mode": "persistent", "id": str(start.thread_id)}
    # A list carries the run's options; a dict alone would be the run's state.
    return [started]
