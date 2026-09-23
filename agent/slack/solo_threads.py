"""Mention-free follow-ups in Slack threads with only one human participant."""

import asyncio
import logging
from time import time

from langgraph_sdk.client import LangGraphClient

from agent.slack.client import slack_thread_mutation_lock
from agent.slack.http import SlackClient
from agent.slack.payloads import SlackMessage
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

SOLO_THREAD_IDLE_SECONDS = 24 * 60 * 60
_NAMESPACE = "slack_solo_threads"
_MAX_HISTORY_PAGES = 5


async def _thread_is_solo(channel_id: str, thread_ts: str, user_id: str) -> bool | None:
    cursor = ""
    seen_cursors: set[str] = set()
    saw_root = False
    async with SlackClient.bot() as slack:
        for _ in range(_MAX_HISTORY_PAGES):
            page = await slack.conversations_replies(
                channel=channel_id, ts=thread_ts, limit=200, cursor=cursor
            )
            messages = page.get("messages")
            if not isinstance(messages, list) or not messages:
                return None
            for raw in messages:
                message = SlackMessage.parse(raw)
                if message is None:
                    return None
                saw_root = saw_root or message.ts == thread_ts
                if message.is_from_bot or message.is_noise:
                    continue
                if not message.user:
                    return None
                if message.user != user_id:
                    return False
            metadata = page.get("response_metadata", {})
            cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
            if not cursor:
                return True if saw_root and not page.get("has_more") else None
            if not isinstance(cursor, str) or cursor in seen_cursors:
                return None
            seen_cursors.add(cursor)
    return None


async def allow_solo_thread_followup(
    client: LangGraphClient,
    *,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    user_id: str,
    explicit_mention: bool,
) -> bool:
    """Track human participation and fail closed when solo routing cannot be verified."""
    namespace = (_NAMESPACE, channel_id)
    try:
        timestamp = float(message_ts)
        if not explicit_mention:
            existing = await client.store.get_item(namespace, thread_ts)
            if not existing:
                return False
        async with (
            asyncio.timeout(2),
            slack_thread_mutation_lock(client, channel_id, thread_ts, purpose="solo-followups"),
        ):
            item = await client.store.get_item(namespace, thread_ts)
            value = item.get("value", {}) if item else {}
            if value.get("blocked"):
                return False
            owner = value.get("user_id")
            if owner and owner != user_id:
                await client.store.put_item(namespace, thread_ts, {"blocked": True})
                return False
            last_seen = value.get("last_seen")
            if not explicit_mention and (
                not isinstance(last_seen, (int, float))
                or timestamp < last_seen
                or timestamp - last_seen > SOLO_THREAD_IDLE_SECONDS
                or time() - timestamp > SOLO_THREAD_IDLE_SECONDS
            ):
                return False
            solo = await _thread_is_solo(channel_id, thread_ts, user_id)
            if solo is False:
                await client.store.put_item(namespace, thread_ts, {"blocked": True})
                return False
            if solo is None:
                logger.warning(
                    "Slack solo-thread history is incomplete",
                    extra={"slack_channel_id": channel_id, "slack_thread_ts": thread_ts},
                )
                return False
            updated: JsonObject = {
                "user_id": user_id,
                "last_seen": max(timestamp, last_seen)
                if isinstance(last_seen, (int, float))
                else timestamp,
            }
            await client.store.put_item(namespace, thread_ts, updated)
            return not explicit_mention
    except Exception:
        logger.warning(
            "Could not verify Slack solo-thread routing",
            extra={"slack_channel_id": channel_id, "slack_thread_ts": thread_ts},
            exc_info=True,
        )
        return False
