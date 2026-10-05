import logging
import re
from typing import Literal, TypedDict

from fastapi import HTTPException

from agent.run_config import RunConfig
from agent.slack.blocks import block_payload
from agent.slack.client import (
    SLACK_USER_ID_RE,
    convert_mentions_to_slack_format,
    get_slack_user_names,
    post_slack_top_level_message_with_ts,
)
from agent.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_error
from agent.slack.markdown import markdown_blocks, markdown_to_mrkdwn
from agent.slack.payloads import SlackChannelPayload
from agent.tools.errors import ToolError
from agent.tools.sandbox_preference import sandbox_only
from agent.users import User

logger = logging.getLogger(__name__)

_CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{1,99}$")


class SlackChannel(TypedDict):
    id: str
    name: str
    is_private: bool


class SlackChannelList(TypedDict):
    success: Literal[True]
    channels: list[SlackChannel]
    next_cursor: str


class SlackChannelMember(TypedDict):
    id: str
    name: str
    github_login: str | None


class SlackChannelMemberList(TypedDict):
    success: Literal[True]
    channel_id: str
    members: list[SlackChannelMember]
    next_cursor: str


class SlackMessageReceipt(TypedDict):
    success: Literal[True]
    channel_id: str
    message_ts: str


@sandbox_only
async def slack_list_channels(cursor: str | None = None) -> SlackChannelList:
    """List a page of public and private channels the Open SWE bot belongs to."""
    try:
        async with SlackClient.bot() as client:
            response = await client.users_conversations(
                types="public_channel,private_channel",
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )
    except HTTPException as exc:
        logger.warning(
            "Slack channel lookup failed", extra={"slack_error": "missing_slack_bot_token"}
        )
        raise ToolError("missing_slack_bot_token") from exc
    except SLACK_REQUEST_ERRORS as exc:
        error = slack_error(exc)
        logger.warning("Slack channel lookup failed", extra={"slack_error": error})
        raise ToolError(error) from exc

    raw_channels: object = response.get("channels")
    if not isinstance(raw_channels, list):
        raise ToolError("invalid_slack_response")
    channels: list[SlackChannel] = []
    for channel in raw_channels:
        if not isinstance(channel, dict):
            raise ToolError("invalid_slack_response")
        channel_id = channel.get("id")
        name = channel.get("name")
        is_private = channel.get("is_private")
        if (
            not isinstance(channel_id, str)
            or not _CHANNEL_ID_RE.fullmatch(channel_id)
            or not isinstance(name, str)
            or not isinstance(is_private, bool)
        ):
            raise ToolError("invalid_slack_response")
        channels.append({"id": channel_id, "name": name, "is_private": is_private})

    metadata: object = response.get("response_metadata") or {}
    next_cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else None
    if not isinstance(next_cursor, str):
        raise ToolError("invalid_slack_response")
    return {"success": True, "channels": channels, "next_cursor": next_cursor}


@sandbox_only
async def slack_list_channel_members(
    channel_id: str, cursor: str | None = None
) -> SlackChannelMemberList:
    """List one page of members of a public channel or this thread's own channel."""
    channel_id = channel_id.strip()
    if not _CHANNEL_ID_RE.fullmatch(channel_id):
        raise ToolError("channel_id must be a Slack channel ID")
    own = RunConfig.from_runtime().slack_thread
    try:
        async with SlackClient.bot() as client:
            info = await client.conversations_info(channel=channel_id)
            channel = SlackChannelPayload.of(info.get("channel"))
            if (
                channel.is_im
                or channel.is_mpim
                or (not channel.is_public and not (own and own.channel_id == channel_id))
            ):
                raise ToolError("Only public channels or this thread's own channel can be listed.")
            response = await client.conversations_members(
                channel=channel_id, limit=200, cursor=cursor
            )
    except HTTPException as exc:
        logger.warning(
            "Slack member lookup failed", extra={"slack_error": "missing_slack_bot_token"}
        )
        raise ToolError("missing_slack_bot_token") from exc
    except SLACK_REQUEST_ERRORS as exc:
        error = slack_error(exc)
        logger.warning("Slack member lookup failed", extra={"slack_error": error})
        raise ToolError(error) from exc

    raw_members: object = response.get("members")
    if not isinstance(raw_members, list):
        raise ToolError("invalid_slack_response")
    member_ids: list[str] = []
    for member in raw_members:
        if not isinstance(member, str) or not SLACK_USER_ID_RE.fullmatch(member):
            raise ToolError("invalid_slack_response")
        member_ids.append(member)
    metadata: object = response.get("response_metadata") or {}
    next_cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else None
    if not isinstance(next_cursor, str):
        raise ToolError("invalid_slack_response")
    names = await get_slack_user_names(member_ids)
    members: list[SlackChannelMember] = [
        {"id": member, "name": names[member], "github_login": await User.login_for_slack(member)}
        for member in member_ids
    ]
    return {
        "success": True,
        "channel_id": channel_id,
        "members": members,
        "next_cursor": next_cursor,
    }


async def slack_post_message(channel_id: str, message: str) -> SlackMessageReceipt:
    """Post a standalone message to a channel the Open SWE bot belongs to."""
    channel_id = channel_id.strip()
    if not _CHANNEL_ID_RE.fullmatch(channel_id):
        raise ToolError("channel_id must be a Slack channel ID")
    if not message.strip():
        raise ToolError("message is required")
    message = convert_mentions_to_slack_format(message)
    if len(message) > 40_000:
        raise ToolError("msg_too_long")
    # conversations.info does not guarantee an is_member field.
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        page = await slack_list_channels(cursor)
        if any(channel["id"] == channel_id for channel in page["channels"]):
            break
        cursor = page["next_cursor"]
        if not cursor:
            raise ToolError("not_in_channel")
        if cursor in seen_cursors:
            raise ToolError("invalid_slack_response")
        seen_cursors.add(cursor)

    blocks = markdown_blocks(message)
    message_ts, error = await post_slack_top_level_message_with_ts(
        channel_id,
        markdown_to_mrkdwn(message),
        unfurl_links=False,
        unfurl_media=False,
        blocks=block_payload(blocks) if blocks else None,
    )
    if not message_ts:
        raise ToolError(error or "post_failed")
    return {"success": True, "channel_id": channel_id, "message_ts": message_ts}
