"""Wait briefly for the previews Slack adds to pasted message links.

Slack unfurls a link to another Slack message a moment after the message is
posted, so the event Trigger Server delivers has no preview yet. The agent never
follows such a link itself, and a link into a DM or private channel could not be
followed anyway, so the preview is all it can see of the linked message.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Final, cast

from langchain_core.messages import HumanMessage

from managed_deepagents._channels.slack.api import SlackApiError, slack_api, slack_bot_token
from managed_deepagents._channels.slack.files import _slack_binding, get_slack_destination
from managed_deepagents._channels.slack.message_content import render_slack_rich_content
from managed_deepagents._channels.types import ChannelError
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from managed_deepagents._managed_tools import ManagedDeepAgentRuntime
    from managed_deepagents._telemetry.otel.context import LogAttributeValue

LINK_PREVIEW_WAITS: Final = (1.0, 2.0)
"""Delays in seconds before each re-read of the message, about 3s in all."""
# Workspace (``acme.slack.com``) and Enterprise Grid (``acme.enterprise.slack.com``) links.
_MESSAGE_URL: Final = re.compile(
    r"https?://(?:[A-Za-z0-9-]{1,63}\.){1,3}slack\.com/archives/([A-Za-z0-9]{1,32})/p(\d{16})"
)
_MESSAGE_TS: Final = re.compile(r"\d{1,20}\.\d{1,12}")
_MAX_LINKS: Final = 20
_PREVIEW_FLAGS: Final = ("is_msg_unfurl", "is_share", "is_reply_unfurl")
_ACTIONABLE_SLACK_ERRORS: Final = frozenset(
    {
        "account_inactive",
        "channel_not_found",
        "invalid_auth",
        "missing_scope",
        "not_authed",
        "not_in_channel",
        "ratelimited",
        "thread_not_found",
        "token_revoked",
    }
)
"""Slack error codes worth naming in a warning; any other is ``other``."""

Sleep = Callable[[float], Awaitable[None]]


def slack_message_links(text: object) -> set[str]:
    """``channel:ts`` of each Slack message URL in ``text``."""
    links: set[str] = set()
    if not isinstance(text, str):
        return links
    for match in _MESSAGE_URL.finditer(text):
        raw = match.group(2)
        links.add(f"{match.group(1)}:{raw[:10]}.{raw[10:]}")
        if len(links) >= _MAX_LINKS:
            break
    return links


def _preview_source(attachment: object) -> str | None:
    """The linked message a preview shows, as ``channel:ts``."""
    if not isinstance(attachment, Mapping) or not any(
        attachment.get(flag) is True for flag in _PREVIEW_FLAGS
    ):
        return None
    return next(iter(slack_message_links(attachment.get("from_url"))), None)


def _previewed_links(attachments: object) -> set[str]:
    if not isinstance(attachments, list):
        return set()
    return {source for item in attachments if (source := _preview_source(item))}


def _warn_failure(error: BaseException, kept: int) -> None:
    """Log a failed read with its bounded channel code and Slack error, never the message."""
    attributes: dict[str, LogAttributeValue] = {"previews_kept": kept}
    reason = ""
    if isinstance(error, ChannelError):
        attributes["channel_error_code"] = reason = error.code
    if isinstance(error, SlackApiError):
        attributes["provider_error"] = reason = (
            error.provider_error if error.provider_error in _ACTIONABLE_SLACK_ERRORS else "other"
        )
    logger.warning(
        "slack link previews could not be read (%s%s)",
        logger.safe_error_label(error),
        f" {reason}" if reason else "",
        attributes=attributes,
    )


async def await_link_previews(
    raw_event: object,
    *,
    token: Callable[[], Awaitable[str]],
    channel: str,
    thread_ts: str,
    wait: Sleep = asyncio.sleep,
) -> list[Mapping[str, object]] | None:
    """The previews Slack added to ``raw_event``'s message links after it was
    delivered; ``None`` when its links needed none or none arrived. A read that
    fails after some previews arrived keeps them."""
    if not isinstance(raw_event, Mapping):
        return None
    message_ts = raw_event.get("ts")
    if not isinstance(message_ts, str) or not _MESSAGE_TS.fullmatch(message_ts):
        return None
    known = _previewed_links(raw_event.get("attachments"))
    missing = slack_message_links(raw_event.get("text")) - known - {f"{channel}:{message_ts}"}
    if not missing:
        return None
    bot = await token()
    added: list[Mapping[str, object]] = []
    for delay in LINK_PREVIEW_WAITS:
        await wait(delay)
        try:
            reply = await slack_api(
                bot,
                "conversations.replies",
                {
                    "channel": channel,
                    "ts": thread_ts,
                    "latest": message_ts,
                    "oldest": message_ts,
                    "inclusive": "true",
                    "limit": 2,
                },
                http_method="GET",
            )
        except Exception as error:
            if not added:
                raise
            _warn_failure(error, len(added))
            break
        messages = reply.get("messages")
        current = next(
            (
                item
                for item in (messages if isinstance(messages, list) else [])
                if isinstance(item, Mapping) and item.get("ts") == message_ts
            ),
            None,
        )
        if current is None:
            continue
        attachments = current.get("attachments")
        added = [
            cast("Mapping[str, object]", item)
            for item in (attachments if isinstance(attachments, list) else [])
            if (source := _preview_source(item)) and source not in known
        ]
        if missing <= _previewed_links(added):
            break
    return added or None


async def link_previews(
    runtime: ManagedDeepAgentRuntime, wait: Sleep | None = None
) -> HumanMessage | None:
    """A message with the previews Slack added to the current Slack message's
    links after it was delivered, rendered as its other attachments are."""
    binding = _slack_binding(runtime)
    destination = get_slack_destination(runtime)
    event = binding.get("event") if binding else None
    if binding is None or destination is None or event is None or event["type"] != "message":
        return None
    raw_event = binding.get("raw_event")
    try:
        added = await await_link_previews(
            raw_event,
            token=slack_bot_token,
            channel=destination["conversation_id"],
            thread_ts=destination["thread_id"],
            wait=wait or asyncio.sleep,
        )
    except Exception as error:  # noqa: BLE001 - best effort: the run goes on with the message as delivered
        _warn_failure(error, 0)
        return None
    if not added or not isinstance(raw_event, Mapping):
        return None
    preview_event: dict[str, object] = {"attachments": added}
    for key in ("bot_id", "subtype"):
        if raw_event.get(key) is not None:
            preview_event[key] = raw_event[key]
    text = raw_event.get("text")
    content = render_slack_rich_content(preview_event, text if isinstance(text, str) else None)
    if content is None:
        return None
    return HumanMessage(content, id=f"link-previews:{binding['delivery_id']}")


__all__ = ["LINK_PREVIEW_WAITS", "await_link_previews", "link_previews", "slack_message_links"]
