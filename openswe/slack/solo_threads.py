"""Mention-free follow-ups in Slack threads with only one human participant."""

import asyncio
import logging

from langgraph_sdk.client import LangGraphClient

from openswe.slack.client import slack_thread_mutation_lock
from openswe.slack.http import SlackClient
from openswe.slack.payloads import SlackMessage
from openswe.slack.records import SlackSoloThread

logger = logging.getLogger(__name__)

_MAX_HISTORY_PAGES = 5
# A mention's tracking runs after its webhook answers; a follow-up in the same
# thread waits for it so it is judged against what the mention recorded.
_mention_tracking: dict[tuple[str, str], asyncio.Task[bool]] = {}


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
    try:
        timestamp = float(message_ts)
        if not explicit_mention:
            if (pending := _mention_tracking.get((channel_id, thread_ts))) is not None:
                await asyncio.shield(pending)
            if await SlackSoloThread.find(channel_id, thread_ts) is None:
                return False
        async with (
            asyncio.timeout(2),
            slack_thread_mutation_lock(client, channel_id, thread_ts, purpose="solo-followups"),
        ):
            record = await SlackSoloThread.find(channel_id, thread_ts)
            if record is not None and record.blocked:
                return False
            if record is not None and record.user_id != user_id:
                await SlackSoloThread.block(channel_id, thread_ts)
                return False
            last_seen = None if record is None else record.last_seen
            if not explicit_mention and (last_seen is None or timestamp < last_seen):
                return False
            solo = await _thread_is_solo(channel_id, thread_ts, user_id)
            if solo is False:
                await SlackSoloThread.block(channel_id, thread_ts)
                return False
            if solo is None:
                logger.warning(
                    "Slack solo-thread history is incomplete",
                    extra={"slack_channel_id": channel_id, "slack_thread_ts": thread_ts},
                )
                return False
            await SlackSoloThread(
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                last_seen=timestamp if last_seen is None else max(timestamp, last_seen),
            ).save()
            return not explicit_mention
    except Exception:
        logger.warning(
            "Could not verify Slack solo-thread routing",
            extra={"slack_channel_id": channel_id, "slack_thread_ts": thread_ts},
            exc_info=True,
        )
        return False


def track_explicit_mention(
    client: LangGraphClient, *, channel_id: str, thread_ts: str, message_ts: str, user_id: str
) -> None:
    """Record an @mention's sender for solo routing without holding up the reply.

    The mention is answered either way; the record only decides whether later
    untagged messages in the thread count as addressed to Open SWE.
    """
    task = asyncio.create_task(
        allow_solo_thread_followup(
            client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=message_ts,
            user_id=user_id,
            explicit_mention=True,
        )
    )
    key = (channel_id, thread_ts)
    _mention_tracking[key] = task

    def forget(done: asyncio.Task[bool]) -> None:
        if _mention_tracking.get(key) is done:
            del _mention_tracking[key]

    task.add_done_callback(forget)
