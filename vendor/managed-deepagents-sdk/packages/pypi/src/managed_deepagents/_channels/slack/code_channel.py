"""Slack code channels: which deliveries are from one, the key the whole channel
is one conversation under, and the session status the deployment sets on it.

Everything else a code channel shows, such as its context bar, tabs or name,
the agent's own tools set with Slack's API.
https://docs.slack.dev/ai/slack-code/
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from managed_deepagents._channels.slack.api import SlackApiError, slack_api, slack_bot_token
from managed_deepagents._channels.types import ChannelError
from managed_deepagents._log import logger

CodeChannelSessionStatus = Literal["active", "processing", "suspended"]

#: A Slack channel id.
_SLACK_ID = re.compile(r"[A-Z0-9]{1,32}")


def is_code_channel_address(address: object) -> bool:
    """Whether a Trigger address is inside a Slack code channel (``code_channel: true``)."""
    return isinstance(address, Mapping) and address.get("code_channel") is True


def code_channel_conversation(address: object, source_thread_key: str) -> tuple[str, str] | None:
    """The Slack channel a code channel delivery names, and the key the whole
    channel is one conversation under.

    That key is the one Trigger Server sends with a Stop there,
    ``slack:v1:<app>:<team>:<channel>:code``: the app and workspace come from
    the delivery's source key, the channel from its address. ``None`` when the
    address is not a code channel's, or it or the key is malformed. Only what
    managed ingress verified names it, never a model.
    """
    conversation = address.get("conversation_id") if isinstance(address, Mapping) else None
    match source_thread_key.split(":"):
        case ["slack", "v1", app, team, *_] if app.strip() and team.strip():
            app_id, team_id = app.strip(), team.strip()
        case _:
            return None
    if (
        not is_code_channel_address(address)
        or not isinstance(conversation, str)
        or not _SLACK_ID.fullmatch(conversation)
    ):
        return None
    return conversation, f"slack:v1:{app_id}:{team_id}:{conversation}:code"


async def set_code_channel_status(channel_id: str, status: CodeChannelSessionStatus) -> None:
    """Set a code channel session's status; a failure is logged by error code, never raised.

    A code channel is one session: no ``thread_ts``.
    https://docs.slack.dev/reference/methods/agents.sessions.setstatus/
    """
    try:
        await slack_api(
            await slack_bot_token(),
            "agents.sessions.setStatus",
            {"channel_id": channel_id, "status": status},
        )
    except Exception as error:  # noqa: BLE001 - the status is a courtesy
        if isinstance(error, SlackApiError):
            code = logger.log_identifier(error.provider_error)
        elif isinstance(error, ChannelError):
            code = error.code
        else:
            code = logger.safe_error_label(error)
        logger.warning("Slack code channel status %s failed (%s).", status, code)


__all__ = [
    "CodeChannelSessionStatus",
    "code_channel_conversation",
    "is_code_channel_address",
    "set_code_channel_status",
]
