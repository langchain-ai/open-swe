"""Deduplication of Slack Event API deliveries."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections import OrderedDict

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get(
    "LANGGRAPH_URL_PROD", "http://localhost:2024"
)

_LOCAL_CLAIM_LIMIT = 2048
_claimed_event_ids: OrderedDict[str, None] = OrderedDict()
_claim_lock = asyncio.Lock()


def _claim_thread_id(event_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:slack-event:{event_id}"))


def _claim_locally(event_id: str) -> None:
    _claimed_event_ids[event_id] = None
    _claimed_event_ids.move_to_end(event_id)
    while len(_claimed_event_ids) > _LOCAL_CLAIM_LIMIT:
        _claimed_event_ids.popitem(last=False)


def reset_slack_event_claims() -> None:
    _claimed_event_ids.clear()


async def slack_event_already_seen(event_id: str) -> bool:
    """Check whether this process has already claimed the event."""
    return bool(event_id and event_id in _claimed_event_ids)


async def claim_slack_event(event_id: str) -> bool:
    """Atomically claim an event id; fail open when the platform is unavailable."""
    if not event_id:
        return True

    async with _claim_lock:
        if event_id in _claimed_event_ids:
            return False

        claim_thread_id = _claim_thread_id(event_id)
        client = get_client(url=LANGGRAPH_URL)
        try:
            await client.threads.create(thread_id=claim_thread_id, if_exists="raise", ttl=10)
        except Exception:  # noqa: BLE001
            try:
                await client.threads.get(claim_thread_id)
            except Exception:  # noqa: BLE001
                logger.warning("Slack event claim failed for event_id=%s", event_id)
                return True
            _claim_locally(event_id)
            return False

        _claim_locally(event_id)
        return True
