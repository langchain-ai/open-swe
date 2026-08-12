"""Deduplication of Slack Event API deliveries.

Slack redelivers an event up to three times when it doesn't get a 2xx within
three seconds, so every delivery that can start an agent run has to be claimed
exactly once.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

_EVENT_NAMESPACE = "slack_mention_events"
_LOCAL_CLAIM_LIMIT = 2048

_claimed_event_ids: OrderedDict[str, None] = OrderedDict()


def _namespace(channel_id: str) -> tuple[str, str]:
    return (_EVENT_NAMESPACE, channel_id or "unknown")


def _claim_locally(event_id: str) -> None:
    _claimed_event_ids[event_id] = None
    _claimed_event_ids.move_to_end(event_id)
    while len(_claimed_event_ids) > _LOCAL_CLAIM_LIMIT:
        _claimed_event_ids.popitem(last=False)


def reset_slack_event_claims() -> None:
    _claimed_event_ids.clear()


async def slack_event_already_seen(channel_id: str, event_id: str) -> bool:
    """Check-only lookup, for short-circuiting a redelivery before any other work."""
    if not event_id:
        return False
    if event_id in _claimed_event_ids:
        return True
    try:
        item = await get_client(url=LANGGRAPH_URL).store.get_item(_namespace(channel_id), event_id)
    except Exception:
        logger.warning("Slack event lookup failed for event_id=%s", event_id, exc_info=True)
        return False
    return bool(item)


async def claim_slack_event(channel_id: str, event_id: str) -> bool:
    """Claim an event id; False means another delivery already owns this event."""
    if not event_id:
        return True

    # Claimed in-process before the first await so concurrent redeliveries on
    # this instance can't both pass; the store below covers other instances.
    if event_id in _claimed_event_ids:
        return False
    _claim_locally(event_id)

    namespace = _namespace(channel_id)
    try:
        client = get_client(url=LANGGRAPH_URL)
        if await client.store.get_item(namespace, event_id):
            return False
        await client.store.put_item(namespace, event_id, {"event_id": event_id})
    except Exception:
        logger.warning("Slack event claim failed for event_id=%s", event_id, exc_info=True)
    return True
