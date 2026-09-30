"""Interactive cards Open SWE posts in Slack threads, and moving them in and out of the channel."""

import logging
from collections.abc import Awaitable, Callable

from langgraph_sdk import get_client

from agent.run_config import RunConfig
from agent.slack.blocks import Block, block_payload
from agent.slack.client import (
    delete_slack_message,
    get_active_slack_thread,
    post_slack_thread_reply_with_ts,
)

logger = logging.getLogger(__name__)


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
) -> bool:
    """Replace a thread card with a fresh reply, sent to the channel too if ``broadcast``.

    The new card is posted before the old one is deleted, so a failure leaves one card up.
    ``adopt`` records the new timestamp and answers whether the new copy is the one to
    keep; when it is not, the new copy is deleted instead of the old one.
    """
    message_ts, error = await post_slack_thread_reply_with_ts(
        location[0],
        location[1],
        text,
        blocks=block_payload(blocks),
        agent_thread_id=agent_thread_id,
        reply_broadcast=broadcast,
    )
    if not message_ts:
        logger.warning(
            "Failed to repost Slack card",
            extra={"slack_error": error, "broadcast": broadcast},
        )
        return False
    kept = await adopt(message_ts)
    stray = old_ts if kept else message_ts
    if not await delete_slack_message(location[0], stray):
        logger.warning("Left a stray Slack card", extra={"slack_message_ts": stray})
    return kept
