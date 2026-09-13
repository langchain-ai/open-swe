"""Direct-message helpers for user-visible Open SWE notifications."""

import logging
from typing import Any

import httpx2

from agent.slack.client import (
    DEFAULT_HTTP_TIMEOUT,
    SLACK_API_BASE_URL,
    SLACK_BOT_TOKEN,
    slack_headers,
)

logger = logging.getLogger(__name__)


async def open_slack_dm_channel(user_id: str) -> str | None:
    """Open (or reuse) the DM channel between the bot and a user."""
    if not SLACK_BOT_TOKEN:
        return None
    async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/conversations.open",
                headers=slack_headers(),
                json={"users": user_id},
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack conversations.open failed: %s", data.get("error"))
                return None
            channel_id = (
                data.get("channel", {}).get("id") if isinstance(data.get("channel"), dict) else None
            )
            return channel_id if isinstance(channel_id, str) and channel_id else None
        except httpx2.HTTPError:
            logger.exception("Slack conversations.open request failed")
            return None


async def post_slack_dm(
    user_id: str,
    text: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
) -> str | None:
    """Send a DM to a user, returning the message timestamp on success."""
    channel_id = await open_slack_dm_channel(user_id)
    if not channel_id:
        return None
    payload: dict[str, Any] = {"channel": channel_id, "text": text}
    if blocks is not None:
        payload["blocks"] = blocks
    async with httpx2.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as http_client:
        try:
            response = await http_client.post(
                f"{SLACK_API_BASE_URL}/chat.postMessage",
                headers=slack_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning("Slack DM chat.postMessage failed: %s", data.get("error"))
                return None
            message_ts = data.get("ts")
            return message_ts if isinstance(message_ts, str) and message_ts else None
        except httpx2.HTTPError:
            logger.exception("Slack DM chat.postMessage request failed")
            return None
