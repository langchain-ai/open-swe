"""Interactive cards Open SWE posts in Slack threads, and moving them in and out of the channel."""

import logging
from collections.abc import Awaitable, Callable

from langgraph_sdk import get_client

from openswe.run_config import RunConfig
from openswe.slack.blocks import Block, block_payload, context
from openswe.slack.client import (
    delete_slack_message,
    get_active_slack_thread,
    get_slack_permalink,
    post_slack_thread_reply_with_ts,
)
from openswe.slack.http import SlackRequestError
from openswe.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)


async def origin_footer(thread_id: str, location: tuple[str, str] | None = None) -> list[Block]:
    links: list[str] = []
    if url := dashboard_thread_url(thread_id):
        links.append(f"<{url}|Web thread>")
    if location is None and thread_id:
        source = await get_active_slack_thread(get_client(), thread_id)
        if source and source.get("channel_id") and source.get("thread_ts"):
            location = (source["channel_id"], source["thread_ts"])
    if location and (url := await get_slack_permalink(*location)):
        links.append(f"<{url}|Slack thread>")
    return [context(" · ".join(links))] if links else []


async def run_slack_location(cfg: RunConfig, thread_id: str) -> tuple[str, str]:
    """The run's own Slack ``(channel_id, thread_ts)``; either may be empty."""
    slack_thread = await get_active_slack_thread(
        get_client(), thread_id, cfg.slack_thread.dump() if cfg.slack_thread else None
    )
    channel_id = str((slack_thread or {}).get("channel_id") or "")
    thread_ts = str((slack_thread or {}).get("thread_ts") or "")
    if not channel_id and cfg.slack_thread is not None:
        channel_id = cfg.slack_thread.channel_id.strip()
    return channel_id, thread_ts


async def repost_thread_card(
    location: tuple[str, str],
    old_ts: str,
    text: str,
    blocks: list[Block],
    *,
    broadcast: bool,
    agent_thread_id: str | None,
    adopt: Callable[[str], Awaitable[bool]],
    login: str | None = None,
) -> bool:
    """Replace a thread card with a fresh reply, sent to the channel too if ``broadcast``.

    The new card is posted before the old one is deleted, so a failure leaves one card up.
    ``adopt`` records the new timestamp and answers whether the new copy is the one to
    keep; when it is not, the new copy is deleted instead of the old one.
    """
    try:
        message_ts = await post_slack_thread_reply_with_ts(
            location[0],
            location[1],
            text,
            blocks=block_payload(blocks),
            agent_thread_id=agent_thread_id,
            reply_broadcast=broadcast,
            login=login,
        )
    except SlackRequestError as exc:
        logger.warning(
            "Failed to repost Slack card",
            extra={"slack_error": exc.code, "broadcast": broadcast},
        )
        return False
    kept = await adopt(message_ts)
    stray = old_ts if kept else message_ts
    if not await delete_slack_message(location[0], stray):
        logger.warning("Left a stray Slack card", extra={"slack_message_ts": stray})
    return kept
