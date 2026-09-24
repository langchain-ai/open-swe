import logging
import re
from dataclasses import replace
from typing import Annotated, Literal, TypedDict

from fastapi import HTTPException
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.prebuilt import InjectedState

from agent.run_config import RunConfig
from agent.slack.client import (
    convert_mentions_to_slack_format,
    post_slack_top_level_message_with_ts,
)
from agent.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error
from agent.utils.run_usage import summarize_run_usage

logger = logging.getLogger(__name__)

_CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{1,99}$")


class SlackChannel(TypedDict):
    id: str
    name: str
    is_private: bool


class SlackChannelError(TypedDict):
    success: Literal[False]
    error: str


class SlackChannelList(TypedDict):
    success: Literal[True]
    channels: list[SlackChannel]
    next_cursor: str


class SlackMessageReceipt(TypedDict):
    success: Literal[True]
    channel_id: str
    message_ts: str


async def slack_list_channels(cursor: str | None = None) -> SlackChannelList | SlackChannelError:
    """List a page of public and private channels the Open SWE bot belongs to."""
    try:
        async with SlackClient.bot() as client:
            response = await client.users_conversations(
                types="public_channel,private_channel",
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )
    except HTTPException:
        logger.warning(
            "Slack channel lookup failed", extra={"slack_error": "missing_slack_bot_token"}
        )
        return {"success": False, "error": "missing_slack_bot_token"}
    except SLACK_REQUEST_ERRORS as exc:
        error = slack_error(exc)
        logger.warning("Slack channel lookup failed", extra={"slack_error": error})
        return {"success": False, "error": error}

    raw_channels: object = response.get("channels")
    if not isinstance(raw_channels, list):
        return {"success": False, "error": "invalid_slack_response"}
    channels: list[SlackChannel] = []
    for channel in raw_channels:
        if not isinstance(channel, dict):
            return {"success": False, "error": "invalid_slack_response"}
        channel_id = channel.get("id")
        name = channel.get("name")
        is_private = channel.get("is_private")
        if (
            not isinstance(channel_id, str)
            or not _CHANNEL_ID_RE.fullmatch(channel_id)
            or not isinstance(name, str)
            or not isinstance(is_private, bool)
        ):
            return {"success": False, "error": "invalid_slack_response"}
        channels.append({"id": channel_id, "name": name, "is_private": is_private})

    metadata: object = response.get("response_metadata") or {}
    next_cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else None
    if not isinstance(next_cursor, str):
        return {"success": False, "error": "invalid_slack_response"}
    return {"success": True, "channels": channels, "next_cursor": next_cursor}


async def slack_post_message(
    channel_id: str,
    message: str,
    state: Annotated[dict[str, object] | None, InjectedState] = None,
) -> SlackMessageReceipt | SlackChannelError:
    """Post a standalone message to a channel the Open SWE bot belongs to."""
    channel_id = channel_id.strip()
    if not _CHANNEL_ID_RE.fullmatch(channel_id):
        return {"success": False, "error": "channel_id must be a Slack channel ID"}
    if not message.strip():
        return {"success": False, "error": "message is required"}
    message = convert_mentions_to_slack_format(message)
    if len(message) > 40_000:
        return {"success": False, "error": "msg_too_long"}
    # conversations.info does not guarantee an is_member field.
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        page = await slack_list_channels(cursor)
        if not page["success"]:
            return page
        if any(channel["id"] == channel_id for channel in page["channels"]):
            break
        cursor = page["next_cursor"]
        if not cursor:
            return {"success": False, "error": "not_in_channel"}
        if cursor in seen_cursors:
            return {"success": False, "error": "invalid_slack_response"}
        seen_cursors.add(cursor)

    cfg = RunConfig.from_config(var_child_runnable_config.get())
    usage = summarize_run_usage(state)
    if usage is not None:
        usage = replace(usage, reasoning_effort=cfg.resolved_agent_effort)
    message_ts, error = await post_slack_top_level_message_with_ts(
        channel_id,
        message,
        unfurl_links=False,
        unfurl_media=False,
        agent_thread_id=cfg.thread_id,
        usage=usage,
    )
    if not message_ts:
        return {"success": False, "error": error or "post_failed"}
    return {"success": True, "channel_id": channel_id, "message_ts": message_ts}
