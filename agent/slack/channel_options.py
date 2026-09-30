"""The Slack channels the dashboard offers when binding one to a workspace, paged from Slack.

Distinct from :mod:`agent.slack.channels`, the stored per-channel directory: this lists the
whole workspace for a picker and is cached briefly rather than persisted.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel
from slack_sdk.errors import SlackApiError

from agent.slack.http import (
    SlackClient,
    slack_cache_key,
    slack_error,
    slack_http_errors,
    slack_retry_after,
)
from agent.utils import ttl_cache

logger = logging.getLogger(__name__)

DIRECTORY_TTL_SECONDS = 900
# A directory Slack cut short is worth retrying soon.
PARTIAL_DIRECTORY_TTL_SECONDS = 60
_CHANNEL_TYPES = "public_channel,private_channel"
_PAGE_SIZE = 1000
# conversations.list is rate limited well below what a large org needs to walk
# in one request; a short wait is worth it, a long one is not.
_MAX_RATE_LIMIT_WAIT_SECONDS = 5.0
_MAX_RATE_LIMIT_RETRIES = 2

_Fetch = Callable[[str], Awaitable[Any]]


class SlackChannelOption(BaseModel):
    id: str
    name: str
    is_private: bool
    is_member: bool
    is_ext_shared: bool
    is_pending_ext_shared: bool = False
    num_members: int | None = None


class SlackChannelDirectory(BaseModel):
    channels: list[SlackChannelOption]
    # True when only the channels the bot belongs to could be listed, because
    # Slack rate limited the walk over every public channel.
    partial: bool = False


def _rate_limit_wait(exc: Exception) -> float | None:
    """Seconds worth waiting before retrying a rate-limited call, else ``None``."""
    if not slack_error(exc).startswith("rate_limited"):
        return None
    raw = slack_retry_after(exc)
    try:
        delay = float(raw) if raw else 1.0
    except ValueError:
        delay = 1.0
    return delay if 0 <= delay <= _MAX_RATE_LIMIT_WAIT_SECONDS else None


async def _collect(fetch: _Fetch) -> list[dict[str, Any]]:
    """Every channel across the pages ``fetch`` returns, retrying brief rate limits."""
    channels: list[dict[str, Any]] = []
    cursor = ""
    seen_cursors: set[str] = set()
    retries = 0
    while True:
        try:
            page = await fetch(cursor)
        except SlackApiError as exc:
            wait = _rate_limit_wait(exc)
            if wait is None or retries >= _MAX_RATE_LIMIT_RETRIES:
                raise
            retries += 1
            await asyncio.sleep(wait)
            continue
        items = page.get("channels")
        if not isinstance(items, list):
            raise HTTPException(502, "Slack returned an invalid collection.")
        channels.extend(item for item in items if isinstance(item, dict))
        metadata = page.get("response_metadata") or {}
        cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
        if not cursor:
            return channels
        if not isinstance(cursor, str) or cursor in seen_cursors:
            raise HTTPException(502, "Slack returned an invalid pagination cursor.")
        seen_cursors.add(cursor)


def _by_name(option: SlackChannelOption) -> tuple[str, str]:
    return (option.name.casefold(), option.id)


def _channel_option(
    channel: dict[str, Any], *, member: bool | None = None
) -> SlackChannelOption | None:
    channel_id, name = channel.get("id"), channel.get("name")
    if not isinstance(channel_id, str) or not channel_id or not isinstance(name, str) or not name:
        return None
    members = channel.get("num_members")
    return SlackChannelOption(
        id=channel_id,
        name=name,
        is_private=channel.get("is_private") is True,
        is_member=channel.get("is_member") is True if member is None else member,
        is_ext_shared=channel.get("is_ext_shared") is True,
        is_pending_ext_shared=channel.get("is_pending_ext_shared") is True,
        num_members=members if isinstance(members, int) and not isinstance(members, bool) else None,
    )


async def list_slack_channels() -> SlackChannelDirectory:
    """The unarchived channels the bot can see, by name.

    The channels the bot belongs to come first, from ``users.conversations``:
    that call is cheap enough to always complete, and those are the channels a
    mention can reach. Every other public channel is added from
    ``conversations.list`` as far as Slack's rate limit allows; when it does
    not allow the whole walk the directory is marked partial and cached only
    briefly. A private channel the bot has not been invited to is absent
    either way, so the picker keeps an id-entry fallback.
    """
    async with slack_http_errors(), SlackClient.bot() as client:
        key = f"{slack_cache_key(client)}:channel-directory"

        async def load() -> SlackChannelDirectory:
            options: dict[str, SlackChannelOption] = {}
            for channel in await _collect(
                lambda cursor: client.users_conversations(
                    types=_CHANNEL_TYPES, exclude_archived=True, limit=_PAGE_SIZE, cursor=cursor
                )
            ):
                option = _channel_option(channel, member=True)
                if option is not None:
                    options[option.id] = option
            partial = False
            try:
                for channel in await _collect(
                    lambda cursor: client.conversations_list(
                        types=_CHANNEL_TYPES,
                        exclude_archived=True,
                        limit=_PAGE_SIZE,
                        cursor=cursor,
                    )
                ):
                    option = _channel_option(channel)
                    if option is not None and option.id not in options:
                        options[option.id] = option
            except SlackApiError as exc:
                if not slack_error(exc).startswith("rate_limited"):
                    raise
                partial = True
                logger.warning(
                    "Slack rate limited the channel directory; listing only the bot's channels",
                    extra={"slack_error": slack_error(exc)},
                )
            directory = SlackChannelDirectory(
                channels=sorted(options.values(), key=_by_name), partial=partial
            )
            if partial:
                # The cache stores what a loader returns for the full TTL right
                # after it returns, so the shorter expiry has to land on the next
                # loop iteration. Doing it here, rather than after every read,
                # keeps a cache hit from pushing the retry out again and again.
                asyncio.get_running_loop().call_soon(
                    ttl_cache.set_cached, key, directory, PARTIAL_DIRECTORY_TTL_SECONDS
                )
            return directory

        return await ttl_cache.cached_stale_while_revalidate(key, DIRECTORY_TTL_SECONDS, load)
