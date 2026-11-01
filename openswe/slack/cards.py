"""Interactive cards Open SWE posts in Slack threads, and moving them in and out of the channel."""

import logging

from langgraph_sdk import get_client

from openswe.run_config import RunConfig
from openswe.slack.blocks import Block, context
from openswe.slack.client import (
    get_active_slack_thread,
    get_slack_permalink,
)
from openswe.source_context import SlackThreadRef
from openswe.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)


async def origin_footer(
    thread_id: str,
    location: tuple[str, str] | None = None,
    *,
    fallback: SlackThreadRef | None = None,
) -> list[Block]:
    links: list[str] = []
    if url := dashboard_thread_url(thread_id):
        links.append(f"<{url}|Web thread>")
    if location is None and (thread_id or fallback):
        source = await get_active_slack_thread(
            get_client(), thread_id, fallback.dump() if fallback else None
        )
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
