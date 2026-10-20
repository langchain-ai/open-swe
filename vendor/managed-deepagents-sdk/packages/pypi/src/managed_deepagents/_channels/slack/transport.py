"""Slack Web API transport for managed channel egress.

Uses the shared JSON API helper against ``https://slack.com/api/*`` — no Slack SDK.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING

from managed_deepagents._channels.runtime import require_text_content, validate_message
from managed_deepagents._channels.slack.api import SLACK_API, FetchFn, slack_api

if TYPE_CHECKING:
    from managed_deepagents._channels.types import ChannelMessage, JsonValue, PostedChannelMessage


SLACK_API_BASE_ENV = "SLACK_API_BASE"


class SlackWebApiTransport:
    """``ChannelMessagingAdapter`` backed by Slack ``chat.postMessage``."""

    def __init__(
        self,
        *,
        bot_token: str,
        api_base: str | None = None,
        fetch: FetchFn | None = None,
    ) -> None:
        token = bot_token.strip()
        if not token:
            raise ValueError("Slack bot token is required for channel transport")
        self._token = token
        resolved = api_base or os.environ.get(SLACK_API_BASE_ENV) or SLACK_API
        self._api_base = resolved.rstrip("/")
        self._fetch = fetch

    async def post(
        self,
        *,
        message: ChannelMessage,
        target: JsonValue,
        new_thread: bool = False,
    ) -> PostedChannelMessage:
        validate_message(message)
        if not isinstance(target, Mapping) or not isinstance(target.get("conversation_id"), str):
            raise ValueError("invalid Slack target")
        conversation_id = target["conversation_id"]
        body: dict[str, object] = {}
        if message["type"] == "content":
            body["text"] = require_text_content(message)
        else:
            native = message["native"]
            if not isinstance(native, Mapping):
                raise ValueError("Slack native message must be an object")
            _apply_native(body, native)
        body["channel"] = conversation_id
        if not new_thread and isinstance(target.get("thread_id"), str):
            body["thread_ts"] = target["thread_id"]
        result = await slack_api(
            self._token, "chat.postMessage", body, api_base=self._api_base, fetch=self._fetch
        )
        ts = result.get("ts") if isinstance(result.get("ts"), str) else None
        channel = (
            result.get("channel") if isinstance(result.get("channel"), str) else conversation_id
        )
        message_ts = str(ts) if ts else str(uuid.uuid4())
        return {
            "id": f"slack:{channel}:{message_ts}",
            "provider_message_id": message_ts,
        }


def create_slack_web_api_transport(
    *,
    bot_token: str,
    api_base: str | None = None,
    fetch: FetchFn | None = None,
) -> SlackWebApiTransport:
    """Build a Slack Web API channel transport."""
    return SlackWebApiTransport(bot_token=bot_token, api_base=api_base, fetch=fetch)


def _apply_native(body: dict[str, object], payload: Mapping[str, object]) -> None:
    for key in ("text", "blocks", "attachments", "mrkdwn"):
        if key in payload:
            body[key] = payload[key]


__all__ = [
    "SLACK_API_BASE_ENV",
    "SlackWebApiTransport",
    "create_slack_web_api_transport",
]
