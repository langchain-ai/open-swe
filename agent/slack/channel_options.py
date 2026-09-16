"""The Slack channels the dashboard offers when binding one to a workspace, paged from Slack.

Distinct from :mod:`agent.slack.channels`, the stored per-channel directory: this lists the
whole workspace for a picker and is cached briefly rather than persisted.
"""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel
from slack_sdk.web.async_client import AsyncWebClient

from agent.slack.http import slack_cache_key, slack_client, slack_http_errors
from agent.utils import ttl_cache

CHANNEL_DIRECTORY_TTL_SECONDS = 300
_CHANNEL_TYPES = "public_channel,private_channel"


class SlackChannelOption(BaseModel):
    id: str
    name: str
    is_private: bool
    is_member: bool
    is_ext_shared: bool
    num_members: int | None = None


async def _slack_conversations(client: AsyncWebClient) -> AsyncIterator[dict[str, Any]]:
    cursor = ""
    seen_cursors: set[str] = set()
    while True:
        page = await client.conversations_list(
            types=_CHANNEL_TYPES, exclude_archived=True, limit=200, cursor=cursor
        )
        items = page.get("channels")
        if not isinstance(items, list):
            raise HTTPException(502, "Slack returned an invalid collection.")
        for item in items:
            if isinstance(item, dict):
                yield item
        metadata = page.get("response_metadata") or {}
        cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
        if not cursor:
            return
        if not isinstance(cursor, str) or cursor in seen_cursors:
            raise HTTPException(502, "Slack returned an invalid pagination cursor.")
        seen_cursors.add(cursor)


def _channel_option(channel: dict[str, Any]) -> SlackChannelOption | None:
    channel_id, name = channel.get("id"), channel.get("name")
    if not isinstance(channel_id, str) or not channel_id or not isinstance(name, str) or not name:
        return None
    members = channel.get("num_members")
    return SlackChannelOption(
        id=channel_id,
        name=name,
        is_private=channel.get("is_private") is True,
        is_member=channel.get("is_member") is True,
        is_ext_shared=channel.get("is_ext_shared") is True,
        num_members=members if isinstance(members, int) and not isinstance(members, bool) else None,
    )


async def list_slack_channels() -> list[SlackChannelOption]:
    """Every unarchived channel the bot can see, by name.

    Slack lists private channels only where the bot is a member, so a private
    channel it has not been invited to is absent here; the picker keeps an
    id-entry fallback for those. Cached briefly: it is a directory to browse,
    and every binding is validated when the workspace is saved.
    """
    async with slack_http_errors(), slack_client() as client:

        async def load() -> list[SlackChannelOption]:
            options: dict[str, SlackChannelOption] = {}
            async for channel in _slack_conversations(client):
                option = _channel_option(channel)
                if option is not None:
                    options[option.id] = option
            return sorted(options.values(), key=lambda option: (option.name.casefold(), option.id))

        return await ttl_cache.cached(
            f"{slack_cache_key(client)}:channel-directory", CHANNEL_DIRECTORY_TTL_SECONDS, load
        )
