"""The prompt a Slack message's run starts with, as the backend writes it for its own Slack runs.

A copy of the prompt half of ``openswe.slack.webhook._process_slack_mention_impl``
and the ``openswe.slack.client`` helpers it uses (AGENTS.md's exception for channel
input): MDA's Slack handler builds the turn, and the backend only answers what needs
its data and signs the run. The model reads the text, and the backend dedupes
context blocks by it, so ``build_input`` writes what ``_slack_context_input`` does.

Slack is read with this deployment's bot token (``mda/slack-bot-token``). A read
that fails is logged and treated as empty, as on the backend: the turn still starts.
"""

import asyncio
import base64
import logging
import mimetypes
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any, Final, cast
from urllib.parse import urljoin, urlparse

import httpx2
from langchain_core.messages.content import (
    ImageContentBlock,
    TextContentBlock,
    create_image_block,
    create_text_block,
)

# The deployment's Slack client and bot token, as MDA's own Slack channel reads Slack.
from managed_deepagents._channels.slack.api import (  # noqa: PLC2701
    slack_api,
    slack_bot_token,
)
from managed_deepagents._channels.types import ChannelError  # noqa: PLC2701
from runtime_api.loop import VISIBLE_FROM
from runtime_api.slack import SlackNotice

from rt.images import extract_image_urls, fetch_image_block
from rt.input_messages import (
    ChannelIdentity,
    InputMessageContext,
    PersonIdentity,
    RunInput,
    RunMessage,
    SystemIdentity,
    channel_introduction,
    dynamic_context_hash,
    dynamic_context_hashes_from_messages,
    human_input,
    input_message_timestamps,
    person_introduction,
    system_input,
    system_introduction,
)
from rt.prompts import prompt
from rt.public_http import PublicOrigin

logger = logging.getLogger(__name__)

SLACK_THREAD_MAX_MESSAGES: Final = 500
_FORWARDED_ATTACHMENT_MAX_COUNT: Final = 10
_FORWARDED_ATTACHMENT_MAX_DEPTH: Final = 4
_FORWARDED_ATTACHMENT_MAX_NODES: Final = 50
_FORWARDED_ATTACHMENT_TEXT_MAX_CHARS: Final = 8000
# Slack adds a pasted message link's preview a moment after the message is posted.
_UNFURL_WAITS: Final = (1.0, 2.0)
_MESSAGE_TS_RE: Final = re.compile(r"^[0-9]{1,20}(?:\.[0-9]{1,12})?$")
_USER_MENTION_RE: Final = re.compile(r"<@([UW][A-Z0-9]+)>")
_CHANNEL_MENTION_RE: Final = re.compile(r"<#([CG][A-Z0-9]+)\|?>")
_MESSAGE_URL_RE: Final = re.compile(
    r"https?://[a-zA-Z0-9\-]+\.slack\.com/archives/([A-Za-z0-9]+)/p(\d{16})(?:\?[^\s>|]*)?"
)
# Messages that are Slack's own notices rather than something a person said.
_SYSTEM_MESSAGE_SUBTYPES: Final = frozenset(
    {
        "bot_message",
        "channel_archive",
        "channel_join",
        "channel_leave",
        "channel_name",
        "channel_purpose",
        "channel_topic",
        "channel_unarchive",
        "group_join",
        "group_leave",
    }
)
_SLACK_CONTEXT_SENDER_ID: Final = "system:slack-context"
_SLACK_NOTICE_SENDER: Final[SystemIdentity] = {
    "id": "system:slack-notice",
    "display_name": "Robin notices",
    "platform": "slack",
    "content": (
        "Notices Robin posts in the Slack thread itself, not an agent: for example, "
        "where a `/breakout` continues, or that a run failed."
    ),
}
# As the backend's ``openswe.utils.multimodal``; a Slack file needs the bot token.
_MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024
_MAX_REDIRECTS: Final = 5
_IMAGE_TIMEOUT: Final = httpx2.Timeout(30.0, connect=10.0)
_SUPPORTED_IMAGE_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
# Written by the deepagents summarization middleware; the prompt it builds is the
# summary message followed by messages[cutoff_index:].
_SUMMARIZATION_EVENT_KEY: Final = "_summarization_event"

CONCIERGE_CONTEXT: Final = prompt("slack-concierge")
KITCHEN_CONTEXT: Final = prompt("slack-kitchen")
NON_KITCHEN_CONTEXT: Final = prompt("slack-non-kitchen")
MESSAGE_UPDATE_PREAMBLE: Final = prompt("slack-message-update")

type SlackMessage = dict[str, Any]


def parse_ts(ts: str | None) -> float:
    try:
        return float(ts or "0")
    except TypeError, ValueError:
        return 0.0


def message_bot_id(message: Mapping[str, Any]) -> str:
    """The bot identifier on a Slack message, or "" when a person authored it."""
    bot_id = message.get("bot_id")
    if isinstance(bot_id, str) and bot_id.strip():
        return bot_id.strip()
    if message.get("subtype") == "bot_message":
        username = message.get("username")
        return username.strip() if isinstance(username, str) and username.strip() else "unknown"
    user_id = message.get("user")
    return "" if isinstance(user_id, str) and user_id.strip() else "unknown"


def message_bot_name(message: Mapping[str, Any]) -> str:
    """The display name for a bot-authored Slack message."""
    profile = message.get("bot_profile")
    if isinstance(profile, dict):
        name = profile.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    username = message.get("username")
    if isinstance(username, str) and username.strip():
        return username.strip()
    return "Bot"


def is_own_message(message: Mapping[str, Any], bot_user_id: str) -> bool:
    """Whether Open SWE itself posted this Slack message.

    Only the authoring user id proves it: any app may post under Open SWE's
    username, and that must not hide a third party's message from the transcript.
    """
    return bool(bot_user_id) and message.get("user") == bot_user_id


def slack_image_file_urls(message: Mapping[str, object]) -> list[str]:
    """Private URLs of the images attached to a Slack message."""
    files = message.get("files")
    if not isinstance(files, list):
        return []
    return [
        str(file_info["url_private"])
        for file_info in files
        if isinstance(file_info, dict)
        and str(file_info.get("mimetype", "")).startswith("image/")
        and file_info.get("url_private")
    ]


def image_urls(messages: Iterable[Mapping[str, Any]], extra: Iterable[str] = ()) -> list[str]:
    """Each image the messages link to or attach, then ``extra`` (a resolved link's), once."""
    found = list(messages)
    return list(
        dict.fromkeys(
            [
                *(url for message in found for url in extract_image_urls(message.get("text", ""))),
                *(url for message in found for url in slack_image_file_urls(message)),
                *extra,
            ]
        )
    )


def replace_bot_mention_with_username(text: str, bot_user_id: str, bot_username: str) -> str:
    """Replace Slack's ``<@BOT>`` mention token with ``@username``."""
    if not text:
        return ""
    if bot_user_id and bot_username:
        return text.replace(f"<@{bot_user_id}>", f"@{bot_username}")
    return text


def strip_bot_mention(text: str, bot_user_id: str, bot_username: str = "") -> str:
    """Remove the bot's mention token from Slack text."""
    if not text:
        return ""
    stripped = text
    if bot_user_id:
        stripped = stripped.replace(f"<@{bot_user_id}>", "")
    if bot_username:
        stripped = stripped.replace(f"@{bot_username}", "")
    return stripped.strip()


def mentioned_user_ids(text: str) -> list[str]:
    """The user ids in bare ``<@USER_ID>`` mentions."""
    return _USER_MENTION_RE.findall(text)


def mentioned_channel_ids(text: str) -> list[str]:
    """The channel ids in ``<#CHANNEL_ID>`` mentions that carry no name."""
    return _CHANNEL_MENTION_RE.findall(text)


def _label(text: str, pattern: re.Pattern[str], sigil: str, names_by_id: Mapping[str, str]) -> str:
    def label(match: re.Match[str]) -> str:
        entity_id = match[1]
        name = names_by_id.get(entity_id, "")
        if not name or name == entity_id:
            return match[0]
        escaped = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<{sigil}{entity_id}|{escaped}>"

    return pattern.sub(label, text)


def label_user_mentions(text: str, user_names_by_id: Mapping[str, str]) -> str:
    """Rewrite bare ``<@USER_ID>`` mentions to Slack's labelled ``<@USER_ID|name>`` form."""
    return _label(text, _USER_MENTION_RE, "@", user_names_by_id)


def label_channel_mentions(text: str, channel_names_by_id: Mapping[str, str]) -> str:
    """Rewrite unnamed ``<#CHANNEL_ID>`` mentions to ``<#CHANNEL_ID|name>``."""
    return _label(text, _CHANNEL_MENTION_RE, "#", channel_names_by_id)


def label_mentions(
    text: str, user_names_by_id: Mapping[str, str], channel_names_by_id: Mapping[str, str]
) -> str:
    """Name both the people and the channels ``text`` mentions."""
    return label_channel_mentions(label_user_mentions(text, user_names_by_id), channel_names_by_id)


def mentions_open_swe(text: object, bot_user_id: str, bot_username: str = "") -> bool:
    """Whether ``text`` mentions Open SWE, by its user id or its username."""
    return isinstance(text, str) and bool(
        (bot_user_id and f"<@{bot_user_id}>" in text)
        or (bot_username and f"@{bot_username}" in text)
    )


def select_context_messages(
    messages: list[SlackMessage],
    current_message_ts: str,
    bot_user_id: str,
    bot_username: str = "",
    *,
    treat_all_messages_as_mentions: bool = False,
    held_timestamps: Collection[str] | None = None,
) -> tuple[list[SlackMessage], str]:
    """The messages since the thread started or since the bot was last asked, and which.

    With ``held_timestamps``, the Slack messages the conversation's thread holds, only a
    mention it holds starts the context: a ``/btw`` or ``/breakout`` went to another
    thread, and a new thread holds nothing, so it reads the Slack thread from its start.
    """
    if not messages:
        return [], "thread_start"

    current_ts = parse_ts(current_message_ts)
    ordered = sorted(messages, key=lambda item: parse_ts(item.get("ts")))
    up_to_current = [item for item in ordered if parse_ts(item.get("ts")) <= current_ts]
    if not up_to_current:
        up_to_current = ordered

    mention_tokens = []
    if bot_user_id:
        mention_tokens.append(f"<@{bot_user_id}>")
    if bot_username:
        mention_tokens.append(f"@{bot_username}")
    if not mention_tokens and not treat_all_messages_as_mentions:
        return up_to_current, "thread_start"

    last_mention_index = -1
    for index, message in enumerate(up_to_current[:-1]):
        text = message.get("text", "")
        is_explicit_mention = isinstance(text, str) and any(
            token in text for token in mention_tokens
        )
        user_id = message.get("user")
        is_user_message = (
            isinstance(user_id, str)
            and bool(user_id)
            and user_id != bot_user_id
            and not message.get("bot_id")
            and not message.get("bot_profile")
        )
        held = held_timestamps is None or str(message.get("ts", "")) in held_timestamps
        if held and (is_explicit_mention or (treat_all_messages_as_mentions and is_user_message)):
            last_mention_index = index

    if last_mention_index >= 0:
        return up_to_current[last_mention_index:], "last_mention"
    return up_to_current, "thread_start"


def _is_forwarded_attachment(attachment: Mapping[str, object]) -> bool:
    return any(
        attachment.get(flag) is True for flag in ("is_share", "is_msg_unfurl", "is_reply_unfurl")
    )


def _non_empty_strings(values: Iterable[object]) -> list[str]:
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def _list_of_dicts(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _block_texts(blocks: object) -> list[str]:
    """Text of Block Kit blocks, skipping action buttons."""
    texts: list[str] = []
    for block in _list_of_dicts(blocks):
        if block.get("type") == "actions":
            continue
        items = [block.get("text"), *_list_of_dicts(block.get("fields"))]
        items += _list_of_dicts(block.get("elements"))
        texts += _non_empty_strings(item.get("text") for item in items if isinstance(item, dict))
    return texts


def _attachment_texts(attachment: dict[str, object]) -> list[str]:
    values = [attachment.get(key) for key in ("pretext", "title", "title_link", "text")]
    for field in _list_of_dicts(attachment.get("fields")):
        values += [field.get("title"), field.get("value")]
    texts = _non_empty_strings(values) + _block_texts(attachment.get("blocks"))
    return texts or _non_empty_strings([attachment.get("fallback")])


def _card_text(message: Mapping[str, object]) -> str:
    """Readable text of an app message built from blocks or attachments instead of ``text``."""
    texts = _block_texts(message.get("blocks"))
    for attachment in _list_of_dicts(message.get("attachments")):
        if not _is_forwarded_attachment(attachment):
            texts += _attachment_texts(attachment)
    return "\n".join(texts)[:_FORWARDED_ATTACHMENT_TEXT_MAX_CHARS]


def format_forwarded_attachments(attachments: object) -> str:
    """The messages forwarded into a Slack message, as prompt text."""
    forwarded: list[str] = []
    rendered_count = 0
    visited_count = 0

    def visit(values: object, depth: int) -> None:
        nonlocal rendered_count, visited_count
        if depth > _FORWARDED_ATTACHMENT_MAX_DEPTH or not isinstance(values, list):
            return

        for attachment in values:
            if (
                rendered_count >= _FORWARDED_ATTACHMENT_MAX_COUNT
                or visited_count >= _FORWARDED_ATTACHMENT_MAX_NODES
            ):
                return
            visited_count += 1
            if not isinstance(attachment, dict):
                continue

            if _is_forwarded_attachment(attachment):
                author = attachment.get("author_name")
                author = author.strip() if isinstance(author, str) else ""
                content = attachment.get("text")
                if not isinstance(content, str) or not content.strip():
                    content = attachment.get("fallback")
                content = content.strip() if isinstance(content, str) else ""
                if len(content) > _FORWARDED_ATTACHMENT_TEXT_MAX_CHARS:
                    content = (
                        content[:_FORWARDED_ATTACHMENT_TEXT_MAX_CHARS].rstrip() + "… [truncated]"
                    )
                source = attachment.get("from_url")
                source = source.strip() if isinstance(source, str) else ""

                label = "[Forwarded Slack message"
                if author:
                    label += f" from {author}"
                label += "]"
                parts = [label]
                if content:
                    parts.append(content)
                if source:
                    parts.append(f"Source: {source}")
                if len(parts) > 1:
                    indentation = "  " * depth
                    forwarded.append("\n".join(f"{indentation}{part}" for part in parts))
                    rendered_count += 1

            visit(attachment.get("attachments"), depth + 1)

    visit(attachments, 0)
    return "\n".join(forwarded)


def _thread_reply_marker(message: Mapping[str, Any]) -> str:
    """A pointer to the replies hanging off a channel message, when it has any."""
    raw_thread_ts = message.get("thread_ts")
    thread_ts = raw_thread_ts.strip() if isinstance(raw_thread_ts, str) else ""
    reply_count = message.get("reply_count")
    if not _MESSAGE_TS_RE.fullmatch(thread_ts) or not isinstance(reply_count, int):
        return ""
    if reply_count < 1:
        return ""
    plural = "reply" if reply_count == 1 else "replies"
    return f" [thread: {reply_count} {plural}, thread_ts={thread_ts}]"


def format_messages_for_prompt(
    messages: list[SlackMessage],
    user_names_by_id: Mapping[str, str] | None = None,
    bot_user_id: str = "",
    bot_username: str = "",
    *,
    include_thread_replies: bool = False,
) -> str:
    """Slack messages, forwarded context included, as readable prompt text."""
    if not messages:
        return "(no thread messages available)"

    lines: list[str] = []
    for message in messages:
        forwarded = format_forwarded_attachments(message.get("attachments"))
        text = label_user_mentions(
            replace_bot_mention_with_username(
                str(message.get("text", "")),
                bot_user_id=bot_user_id,
                bot_username=bot_username,
            ),
            user_names_by_id or {},
        ).strip() or _card_text(message)
        text = text or ("[forwarded message]" if forwarded else "[non-text message]")
        user_id = message.get("user")
        if is_own_message(message, bot_user_id):
            author = f"@{bot_username or 'Robin'}(self)"
        elif message_bot_id(message):
            author = f"@{message_bot_name(message)}(bot)"
        else:
            author_name = (user_names_by_id or {}).get(str(user_id)) or str(user_id)
            author = f"@{author_name}({user_id})"
        raw_message_ts = message.get("ts")
        message_ts = raw_message_ts.strip() if isinstance(raw_message_ts, str) else ""
        identifier = f" [message_ts={message_ts}]" if _MESSAGE_TS_RE.fullmatch(message_ts) else ""
        replies = _thread_reply_marker(message) if include_thread_replies else ""
        line = f"{author}{identifier}{replies}: {text}"
        if forwarded:
            line += f"\n{forwarded}"
        lines.append(line)
    return "\n".join(lines)


def participant_ids(messages: Iterable[Mapping[str, object]]) -> set[str]:
    """The people who spoke in the messages, Slack's own notices and bots left out."""
    return {
        user_id
        for message in messages
        if not message.get("bot_id")
        and not message.get("bot_profile")
        and message.get("subtype") not in _SYSTEM_MESSAGE_SUBTYPES
        and isinstance(user_id := message.get("user"), str)
        and user_id
    }


def channel_context(channel_id: str, payload: Mapping[str, Any]) -> dict[str, str]:
    """A ``conversations.info`` channel's name, topic and purpose, as the backend keeps them."""

    def section(key: str) -> str:
        raw = payload.get(key)
        if isinstance(raw, Mapping):
            value = raw.get("value")
            return value.strip() if isinstance(value, str) else ""
        return raw.strip() if isinstance(raw, str) else ""

    def text(key: str) -> str:
        value = payload.get(key)
        return value.strip() if isinstance(value, str) else ""

    topic, purpose = section("topic"), section("purpose")
    return {
        "id": channel_id,
        "name": text("name"),
        "name_normalized": text("name_normalized"),
        "topic": topic,
        "purpose": purpose,
        "description": "\n".join(value for value in (topic, purpose) if value),
    }


def channel_identity(
    channel_id: str,
    thread_ts: str,
    channel: Mapping[str, Any],
    *,
    default_repo: str = "",
    web_url: str = "",
    trace_url: str = "",
) -> ChannelIdentity:
    """Everything that stays true of this Slack thread, for its context block.

    ``channel`` is the channel's context (``channel_context``, or the backend's
    ``SlackChannelContext`` dump); the repository and links are the backend's.
    """

    def text(key: str) -> str:
        value = channel.get(key)
        return value.strip() if isinstance(value, str) else ""

    identity: ChannelIdentity = {"id": f"slack:{channel_id}", "platform": "slack"}
    channel_name = text("name_normalized") or text("name")
    if channel_name:
        identity["name"] = f"#{channel_name}"
    if thread_ts:
        identity["thread_id"] = thread_ts
    topic, purpose = text("topic"), text("purpose")
    if topic:
        identity["topic"] = topic
    if purpose:
        identity["purpose"] = purpose
    # Slack's own `description` is usually the topic and purpose run together.
    if not topic and not purpose and text("description"):
        identity["description"] = text("description")
    if default_repo:
        identity["default_repo"] = default_repo
    if web_url:
        identity["web_url"] = web_url
    if trace_url:
        identity["trace_url"] = trace_url
    return identity


def thread_constant_context(
    *, review_guide: bool, concierge: bool, kitchen: bool, ask: bool = False
) -> str:
    """Guidance that holds for the whole thread, which the model is told once."""
    return "\n\n".join(
        section
        for section in (
            CONCIERGE_CONTEXT if concierge else "",
            prompt("slack-ask") if ask else KITCHEN_CONTEXT if kitchen else NON_KITCHEN_CONTEXT,
        )
        if section
    )


def dm_origin_section(origin: Mapping[str, str], *, permalink: str, agent_thread_id: str) -> str:
    """What a thread started under a DM Open SWE sent for another thread is about.

    ``origin`` has the other thread's ``channel_id`` and the DM's ``subject``.
    """
    return prompt(
        "slack-dm-origin", origin=origin, permalink=permalink, agent_thread_id=agent_thread_id
    )


def review_request_section(pull_request: Mapping[str, str]) -> str:
    """A reply to a review-request card is about its pull request (``url``, ``title``)."""
    return prompt("slack-review-request", pull_request=pull_request)


def thread_turn_context(
    *,
    message_update: bool = False,
    dm_origin: str = "",
    request: str = "",
    review_request: str = "",
    resolved_links: str = "",
) -> str:
    """What this turn adds, in the order the backend writes it."""
    return "\n\n".join(
        section
        for section in (
            MESSAGE_UPDATE_PREAMBLE if message_update else "",
            dm_origin,
            request,
            review_request,
            resolved_links,
        )
        if section
    )


def visible_dynamic_context_hashes(values: Mapping[str, Any]) -> set[str]:
    """Context hashes the model can still see, so the rest is reintroduced.

    Summarization replaces everything before ``cutoff_index`` with a summary, so a
    context block behind the cutoff is gone from the prompt while still in state.
    """
    messages = values.get("messages")
    if not isinstance(messages, list | tuple):
        return set()
    event = values.get(_SUMMARIZATION_EVENT_KEY)
    cutoff = event.get("cutoff_index") if isinstance(event, Mapping) else values.get(VISIBLE_FROM)
    if isinstance(cutoff, int) and cutoff >= 0:
        messages = messages[cutoff:]
    return dynamic_context_hashes_from_messages(messages)


def dispatched_context(state: Mapping[str, Any] | None) -> tuple[set[str], set[str]]:
    """What the thread already holds: visible context hashes and replayed Slack ts.

    Read from the state ``await thread.get_state()`` gives the handler, as Agent
    Server's ``threads.get_state`` does; a thread no run started on yet (``None``)
    holds nothing.
    """
    values = state.get("values") if isinstance(state, Mapping) else None
    if not isinstance(values, Mapping):
        return set(), set()
    messages = values.get("messages")
    if not isinstance(messages, list):
        return set(), set()
    timestamps: set[str] = set()
    for message in messages:
        if isinstance(message, dict):
            timestamps |= input_message_timestamps(message.get("content"))
    return visible_dynamic_context_hashes(values), timestamps


def slack_person(
    user_id: str, name: str = "", github_login: str = "", person_id: str = ""
) -> PersonIdentity:
    person: PersonIdentity = {
        "id": person_id or f"slack:{user_id}",
        "open_swe_account": "linked" if person_id or github_login else "unlinked",
    }
    if name:
        person["display_name"] = name
    if github_login:
        person["github_login"] = github_login
    return person


def bot_system_identity(bot_id: str, name: str) -> SystemIdentity:
    """Another app posting in the thread, such as an allowed bot that asked Open SWE."""
    return {
        "id": f"system:slack-bot-{bot_id}",
        "display_name": name,
        "platform": "slack",
        "sender_type": "bot",
    }


def _sender(
    message: Mapping[str, Any],
    user_names_by_id: Mapping[str, str],
    logins_by_user_id: Mapping[str, str],
    person_ids_by_user_id: Mapping[str, str] | None = None,
) -> tuple[str, PersonIdentity | SystemIdentity, str]:
    """A thread message's sender id, identity, and kind.

    Open SWE's own replies never reach here, so a message with a ``bot_id`` is
    somebody else's app.
    """
    bot_id = message_bot_id(message)
    if bot_id:
        bot = bot_system_identity(bot_id, message_bot_name(message))
        return bot["id"], bot, "system"
    user_id = str(message.get("user"))
    person = slack_person(
        user_id,
        user_names_by_id.get(user_id, ""),
        logins_by_user_id.get(user_id, ""),
        (person_ids_by_user_id or {}).get(user_id, ""),
    )
    return person["id"], person, "human"


def message_text(
    message: Mapping[str, Any],
    bot_user_id: str,
    user_names_by_id: Mapping[str, str],
    channel_names_by_id: Mapping[str, str],
    bot_username: str = "",
) -> str:
    """One message's text as the prompt shows it, without its author."""
    forwarded = format_messages_for_prompt(
        [dict(message)], {}, bot_user_id=bot_user_id, bot_username=bot_username
    )
    _, separator, content = forwarded.partition(": ")
    return label_mentions(
        content if separator else forwarded, user_names_by_id, channel_names_by_id
    )


def build_input(
    messages: list[SlackMessage],
    user_names_by_id: Mapping[str, str],
    logins_by_user_id: Mapping[str, str],
    *,
    person_ids_by_user_id: Mapping[str, str] | None = None,
    channel_names_by_id: Mapping[str, str] | None = None,
    channel: ChannelIdentity,
    bot_user_id: str,
    event_ts: str,
    trigger_user_id: str = "",
    request_text: str,
    request_blocks: list[dict[str, Any]],
    prior_message_text: str = "",
    is_breakout: bool = False,
    turn_context: str = "",
    constant_context: str = "",
    dispatched_timestamps: set[str] | None = None,
    run_described_person_ids: set[str] | None = None,
    visible_context_hashes: set[str] | None = None,
    trigger_system: SystemIdentity | None = None,
    explicit_mention: bool = False,
    web_only: bool = False,
    bot_username: str = "",
    source_message_ts: str = "",
    context_channel_id: str = "",
) -> RunInput:
    """The run's input for a Slack message: who and where, what came before it, then it.

    ``bot_username`` is the backend's ``SLACK_BOT_USERNAME``, which it reads globally.
    Each message names its Slack timestamp (``slack_message_ts``) so the dashboard links
    to it, unless the history comes from another channel (``context_channel_id``).
    """
    channel_entity_id = channel["id"]
    link_source = not context_channel_id or context_channel_id == channel_entity_id.removeprefix(
        "slack:"
    )
    channel_names = channel_names_by_id or {}
    already_dispatched = dispatched_timestamps or set()
    visible = set(visible_context_hashes or ())
    run_messages: list[RunMessage] = []

    def add_context(message: RunMessage) -> None:
        """Append a context block unless the model can already see that content."""
        context_hash = dynamic_context_hash(message["content"])
        if context_hash is not None:
            if context_hash in visible:
                return
            visible.add(context_hash)
        run_messages.append(message)

    add_context(channel_introduction(channel))
    # An edit's `event_ts` matches no message, and a click passes the ts of Open SWE's
    # own button message, so the caller says who triggered the run.
    trigger_id = trigger_user_id or next(
        (
            str(message.get("user"))
            for message in messages
            if str(message.get("ts", "")) == str(event_ts)
            and message.get("user")
            and not is_own_message(message, bot_user_id)
            and not message_bot_id(message)
        ),
        "unknown",
    )
    trigger_person = slack_person(
        trigger_id,
        user_names_by_id.get(trigger_id, ""),
        logins_by_user_id.get(trigger_id, ""),
        (person_ids_by_user_id or {}).get(trigger_id, ""),
    )
    # The run describes the trigger sender and every other person it knows, so only
    # a replayed author nobody else accounts for is introduced here.
    described: set[str] = {trigger_person["id"], *(run_described_person_ids or ())}
    for message in messages:
        timestamp = str(message.get("ts", ""))
        # The model has every message an earlier run got.
        if timestamp == str(event_ts) or timestamp in already_dispatched:
            continue
        sender_id: str
        identity: PersonIdentity | SystemIdentity
        kind: str
        if not web_only and is_own_message(message, bot_user_id):
            # The model wrote its own replies and already has them; a notice it never saw.
            if not SlackNotice.marks(message):
                continue
            sender_id, identity, kind = _SLACK_NOTICE_SENDER["id"], _SLACK_NOTICE_SENDER, "system"
        else:
            sender_id, identity, kind = _sender(
                message, user_names_by_id, logins_by_user_id, person_ids_by_user_id
            )
        if sender_id not in described:
            described.add(sender_id)
            add_context(
                person_introduction(cast(PersonIdentity, identity))
                if kind == "human"
                else system_introduction(cast(SystemIdentity, identity))
            )
        message_context: InputMessageContext = {
            "sender_id": sender_id,
            "channel_id": channel_entity_id,
            "surface": "slack",
            "kind": "human" if kind == "human" else "system",
            "data": {
                "timestamp": timestamp,
                **({"slack_message_ts": timestamp} if link_source else {}),
                "explicit_bot_mention": str(
                    mentions_open_swe(message.get("text"), bot_user_id, bot_username)
                ).lower(),
            },
        }
        text = message_text(message, bot_user_id, user_names_by_id, channel_names, bot_username)
        run_messages.append(
            human_input(text, message_context)
            if kind == "human"
            else system_input(text, message_context)
        )
    if prior_message_text:
        run_messages.append(
            human_input(
                label_mentions(prior_message_text, user_names_by_id, channel_names),
                {
                    "sender_id": trigger_person["id"],
                    "channel_id": channel_entity_id,
                    "surface": "slack",
                    "kind": "human",
                    "data": {
                        "timestamp": event_ts,
                        **(
                            {"slack_message_ts": source_message_ts}
                            if source_message_ts and link_source
                            else {}
                        ),
                        "explicit_bot_mention": str(
                            mentions_open_swe(prior_message_text, bot_user_id, bot_username)
                        ).lower(),
                    },
                },
            )
        )
    if constant_context or turn_context:
        slack_context: SystemIdentity = {
            "id": _SLACK_CONTEXT_SENDER_ID,
            "display_name": "Slack context",
            "platform": "slack",
        }
        if constant_context:
            slack_context["content"] = constant_context
        add_context(system_introduction(slack_context))
    if turn_context:
        run_messages.append(
            system_input(
                turn_context,
                {
                    "sender_id": _SLACK_CONTEXT_SENDER_ID,
                    "channel_id": channel_entity_id,
                    "surface": "slack",
                    "kind": "system",
                },
            )
        )
    trigger_sender_id = trigger_person["id"]
    trigger_is_system = False
    if trigger_system is not None:
        trigger_sender_id, trigger_is_system = trigger_system["id"], True
        if trigger_sender_id not in described:
            described.add(trigger_sender_id)
            add_context(system_introduction(trigger_system))
    current_message = next(
        (message for message in messages if str(message.get("ts", "")) == str(event_ts)), {}
    )
    if is_breakout or not str(current_message.get("text") or "").strip():
        rendered_request = message_text(
            {**current_message, "text": ""},
            bot_user_id,
            user_names_by_id,
            channel_names,
            bot_username,
        )
        _, _, forwarded_context = rendered_request.partition("\n")
    else:
        forwarded_context = label_mentions(
            format_forwarded_attachments(current_message.get("attachments")),
            user_names_by_id,
            channel_names,
        )
    if forwarded_context:
        request_text = f"{request_text}\n{forwarded_context}"
    request_blocks[0] = {**request_blocks[0], "text": request_text}
    trigger_context: InputMessageContext = {
        "sender_id": trigger_sender_id,
        "channel_id": channel_entity_id,
        "surface": "web" if web_only else "slack",
        "kind": "system" if trigger_is_system else "human",
        "data": {
            "timestamp": event_ts,
            **(
                {"slack_message_ts": source_message_ts} if source_message_ts and link_source else {}
            ),
            "explicit_bot_mention": str(explicit_mention).lower(),
        },
    }
    blocks = cast(list[dict[str, object]], request_blocks)
    run_messages.append(
        system_input(blocks, trigger_context)
        if trigger_is_system
        else human_input(blocks, trigger_context)
    )
    return {"messages": run_messages}


async def _api(method: str, params: Mapping[str, object]) -> Mapping[str, object] | None:
    """A Slack read with the bot token; ``None``, logged, when Slack refuses or fails it."""
    try:
        return await slack_api(await slack_bot_token(), method, params, http_method="GET")
    except ChannelError as error:
        logger.warning(
            "A Slack read failed",
            extra={"slack_method": method, "slack_error": getattr(error, "provider_error", "")},
        )
        return None


async def slack_history(channel_id: str, thread_ts: str) -> list[SlackMessage]:
    """A Slack thread's messages, oldest first, keeping the most recent window.

    A page that fails ends the read with the pages read so far.
    """
    messages: list[SlackMessage] = []
    cursor = ""
    truncated = False
    while True:
        params: dict[str, object] = {"channel": channel_id, "limit": 200, "ts": thread_ts}
        if cursor:
            params["cursor"] = cursor
        payload = await _api("conversations.replies", params)
        if payload is None:
            break
        batch = payload.get("messages")
        if isinstance(batch, list):
            messages.extend(item for item in batch if isinstance(item, dict))
        if len(messages) >= SLACK_THREAD_MAX_MESSAGES:
            truncated = True
            logger.warning(
                "Slack thread capped at %d messages",
                SLACK_THREAD_MAX_MESSAGES,
                extra={"slack_channel_id": channel_id, "slack_thread_ts": thread_ts},
            )
            break
        metadata = payload.get("response_metadata")
        next_cursor = metadata.get("next_cursor") if isinstance(metadata, Mapping) else ""
        if not isinstance(next_cursor, str) or not next_cursor:
            break
        cursor = next_cursor
    messages.sort(key=lambda item: parse_ts(item.get("ts")))
    if truncated:
        messages = messages[-SLACK_THREAD_MAX_MESSAGES:]
    return messages


def _user_name(user: Mapping[str, Any]) -> str:
    profile = user.get("profile", {})
    if isinstance(profile, dict):
        for key in ("display_name", "real_name"):
            value = profile.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("real_name", "name"):
        value = user.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


async def user_names(user_ids: Iterable[str]) -> dict[str, str]:
    """Display names of Slack users; a user Slack doesn't describe keeps their id."""
    unique_ids = sorted({user_id for user_id in user_ids if isinstance(user_id, str) and user_id})
    payloads = await asyncio.gather(*(_api("users.info", {"user": uid}) for uid in unique_ids))
    names: dict[str, str] = {}
    for user_id, payload in zip(unique_ids, payloads, strict=True):
        user = payload.get("user") if payload is not None else None
        names[user_id] = _user_name(user) if isinstance(user, dict) else user_id
    return names


async def channel_details(channel_id: str) -> Mapping[str, Any] | None:
    """Slack's ``conversations.info`` channel, or ``None`` when Slack doesn't say."""
    if not channel_id:
        return None
    payload = await _api("conversations.info", {"channel": channel_id})
    channel = payload.get("channel") if payload is not None else None
    return channel if isinstance(channel, dict) else None


def _is_public(channel: Mapping[str, Any]) -> bool:
    """Whether anybody in the workspace can already read this channel.

    A name read with the bot token says nothing about who asks, so only a channel
    with no membership to leak counts: not private, not a DM, not shared outside.
    """
    return (
        channel.get("is_channel") is True
        and channel.get("is_private") is False
        and channel.get("is_im") is not True
        and channel.get("is_mpim") is not True
        and channel.get("is_ext_shared") is False
        and channel.get("is_pending_ext_shared") is False
    )


async def public_channel_names(channel_ids: Iterable[str]) -> dict[str, str]:
    """Names of the public channels among ``channel_ids``; a private name never leaves Slack."""
    unique_ids = sorted(set(channel_ids))
    channels = await asyncio.gather(*(channel_details(channel_id) for channel_id in unique_ids))
    return {
        channel_id: name
        for channel_id, channel in zip(unique_ids, channels, strict=True)
        if channel is not None
        and _is_public(channel)
        and isinstance(name := channel.get("name"), str)
        and name.strip()
    }


async def permalink(channel_id: str, message_ts: str) -> str | None:
    """A Slack message's permalink; ``None`` when unavailable."""
    if not channel_id or not message_ts:
        return None
    payload = await _api("chat.getPermalink", {"channel": channel_id, "message_ts": message_ts})
    link = payload.get("permalink") if payload is not None else None
    return link if isinstance(link, str) and link else None


def _message_url(url: str) -> tuple[str, str] | None:
    """A Slack message URL's channel and message ts (``p1776281321762829`` -> ``1776281321.762829``)."""
    match = _MESSAGE_URL_RE.search(url)
    if not match:
        return None
    raw_ts = match.group(2)
    return match.group(1), f"{raw_ts[:10]}.{raw_ts[10:]}"


async def _linked_message(url: str, current_channel_id: str) -> SlackMessage | None:
    """The message ``url`` links to, when whoever posted the link could already read it.

    The bot token reads every channel the bot is in, so a link to another channel is
    followed only when that channel is public (``_is_public``); one to a private channel
    or a DM would put its message in front of everyone in this thread.
    """
    parsed = _message_url(url)
    if parsed is None:
        return None
    channel_id, message_ts = parsed
    if channel_id != current_channel_id:
        channel = await channel_details(channel_id)
        if channel is None or not _is_public(channel):
            return None
    payload = await _api(
        "conversations.history",
        {
            "channel": channel_id,
            "latest": message_ts,
            "oldest": message_ts,
            "inclusive": "true",
            "limit": 1,
        },
    )
    found = payload.get("messages") if payload is not None else None
    if not isinstance(found, list) or not found or not isinstance(found[0], dict):
        return None
    message = found[0]
    resolved: SlackMessage = {
        "channel_id": channel_id,
        "ts": message.get("ts", message_ts),
        "text": message.get("text", ""),
        "user": message.get("user", ""),
        "files": message.get("files", []),
    }
    if message.get("thread_ts"):
        resolved["thread_ts"] = message["thread_ts"]
    return resolved


def _forwarded_sources(messages: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """The Slack messages the forwarded or unfurled attachments of ``messages`` already show."""
    return {
        parsed
        for message in messages
        for attachment in _list_of_dicts(message.get("attachments"))
        if _is_forwarded_attachment(attachment)
        and isinstance(source := attachment.get("from_url"), str)
        and (parsed := _message_url(source))
    }


async def await_link_unfurls(channel_id: str, thread_ts: str, message: SlackMessage) -> None:
    """Wait briefly for Slack to unfurl the message links ``message`` holds, and take the
    previews it adds: the posted copy has none yet, and a link into a DM or private
    channel is never followed, so the preview is all the agent can see of it."""
    links = {
        parsed
        for match in _MESSAGE_URL_RE.finditer(str(message.get("text") or ""))
        if (parsed := _message_url(match.group(0)))
    }
    message_ts = str(message.get("ts") or "")
    if not message_ts:
        return
    # A link ``resolved_links`` can follow needs no preview, and may never get one.
    hidden: set[tuple[str, str]] = set()
    for link in links - _forwarded_sources([message]):
        if link[0] == channel_id:
            continue
        channel = await channel_details(link[0])
        if channel is None or not _is_public(channel):
            hidden.add(link)
    if not hidden:
        return
    for delay in _UNFURL_WAITS:
        await asyncio.sleep(delay)
        payload = await _api(
            "conversations.replies",
            {
                "channel": channel_id,
                "ts": thread_ts or message_ts,
                "latest": message_ts,
                "oldest": message_ts,
                "inclusive": "true",
                "limit": 2,
            },
        )
        found = payload.get("messages") if payload is not None else None
        current = next(
            (
                item
                for item in (found if isinstance(found, list) else [])
                if isinstance(item, dict) and str(item.get("ts")) == message_ts
            ),
            None,
        )
        if current is None:
            continue
        previews = _forwarded_sources([current])
        if previews - _forwarded_sources([message]):
            message["attachments"] = current.get("attachments")
        if hidden <= previews:
            return


async def resolved_links(
    messages: Sequence[Mapping[str, Any]],
    user_names_by_id: Mapping[str, str],
    *,
    channel_id: str,
) -> tuple[str, list[str]]:
    """The Slack messages ``messages`` (in ``channel_id``) link to, as a prompt section,
    and their images. Only links into ``channel_id`` or a public channel are followed."""
    all_text = " ".join(message.get("text", "") for message in messages)
    links = [match.group(0) for match in _MESSAGE_URL_RE.finditer(all_text)]
    if not links:
        return "", []

    shown = _forwarded_sources(messages)
    parts: list[str] = []
    linked_images: list[str] = []
    for link in dict.fromkeys(links):
        try:
            resolved = await _linked_message(link, channel_id)
            if resolved and _message_url(link) in shown:
                # Its preview already quotes it; only its images are missing.
                linked_images.extend(slack_image_file_urls(resolved))
            elif resolved:
                author_id = resolved.get("user", "")
                author = user_names_by_id.get(author_id, author_id)
                if author_id and author == author_id:
                    author = (await user_names([author_id])).get(author_id, author_id)
                text = resolved.get("text", "(empty message)")
                parts.append(f"**{link}**\n  Author: {author}\n  Message: {text}")
                linked_images.extend(slack_image_file_urls(resolved))
            elif _message_url(link) not in shown:
                parts.append(f"**{link}**\n  (Could not fetch — bot may not have access)")
        except Exception:
            logger.exception("Failed to resolve a Slack link")
            parts.append(f"**{link}**\n  (Error resolving link)")

    section = "\n\n## Cross-posted Slack Messages\n" + "\n\n".join(parts) if parts else ""
    return section, linked_images


def _is_slack_file_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "files.slack.com" or host.endswith(".files.slack.com")


class _TooLargeError(ValueError):
    pass


async def _slack_file(url: str, token: str) -> tuple[httpx2.Response, bytes]:
    """A Slack file, with the bot token only on hops that stay on Slack's file host."""
    for _ in range(_MAX_REDIRECTS + 1):
        headers = {"Authorization": f"Bearer {token}"} if _is_slack_file_host(url) else {}
        async with (
            httpx2.AsyncClient(
                transport=PublicOrigin(url, name="image host"),
                timeout=_IMAGE_TIMEOUT,
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            location = response.headers.get("Location")
            if response.is_redirect and location:
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_IMAGE_BYTES:
                    raise _TooLargeError
            return response, bytes(body)
    raise httpx2.TooManyRedirects("too many redirects")


async def _slack_image_block(url: str) -> ImageContentBlock | TextContentBlock | None:
    """An image attached in Slack as a model content block, as ``rt.images`` fetches others."""
    try:
        response, content = await _slack_file(url, await slack_bot_token())
    except _TooLargeError:
        logger.warning("A Slack image exceeds the size limit; skipping it")
        return create_text_block(
            "An attached image was skipped because it exceeded the 10 MiB size limit."
        )
    except Exception as error:  # noqa: BLE001 - a missing image never fails the turn
        logger.warning(
            "Fetching a Slack image failed; skipping it",
            extra={"error_type": type(error).__name__},
        )
        return None
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
    if not content_type:
        content_type, _ = mimetypes.guess_type(url)
    if content_type not in _SUPPORTED_IMAGE_TYPES:
        logger.warning(
            "A Slack image has an unsupported content type; skipping it",
            extra={"content_type": content_type},
        )
        return None
    return create_image_block(
        base64=base64.b64encode(content).decode("ascii"), mime_type=content_type
    )


async def image_blocks(urls: Iterable[str]) -> list[ImageContentBlock | TextContentBlock]:
    """The images at ``urls`` as model content blocks, in order; one that fails is left out."""
    blocks: list[ImageContentBlock | TextContentBlock] = []
    for url in urls:
        block = await (
            _slack_image_block(url) if _is_slack_file_host(url) else fetch_image_block(url)
        )
        if block is not None:
            blocks.append(block)
    return blocks
