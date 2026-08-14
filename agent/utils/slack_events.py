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
_claimed_keys: OrderedDict[str, None] = OrderedDict()
_claim_lock = asyncio.Lock()


def _claim_thread_id(claim_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:slack-event:{claim_key}"))


def slack_message_claim_key(event_id: str, channel_id: str = "", event_ts: str = "") -> str:
    """Key a claim on the message itself, not the delivery.

    Slack fans one tagged channel post out as both `app_mention` and `message`, each
    with its own event id, so an event-id claim lets the same message start two runs.
    """
    if channel_id and event_ts:
        return f"{channel_id}:{event_ts}"
    return event_id


def _claim_locally(claim_key: str) -> None:
    if not claim_key:
        return
    _claimed_keys[claim_key] = None
    _claimed_keys.move_to_end(claim_key)
    while len(_claimed_keys) > _LOCAL_CLAIM_LIMIT:
        _claimed_keys.popitem(last=False)


def reset_slack_event_claims() -> None:
    _claimed_keys.clear()


async def slack_event_already_seen(event_id: str) -> bool:
    """Check whether this process has already claimed the event."""
    return bool(event_id and event_id in _claimed_keys)


async def claim_slack_event(event_id: str, channel_id: str = "", event_ts: str = "") -> bool:
    """Atomically claim a Slack message; fail open when the platform is unavailable."""
    claim_key = slack_message_claim_key(event_id, channel_id, event_ts)
    if not claim_key:
        return True

    async with _claim_lock:
        if claim_key in _claimed_keys or (event_id and event_id in _claimed_keys):
            return False

        claim_thread_id = _claim_thread_id(claim_key)
        client = get_client(url=LANGGRAPH_URL)
        try:
            await client.threads.create(thread_id=claim_thread_id, if_exists="raise", ttl=10)
        except Exception:  # noqa: BLE001
            try:
                await client.threads.get(claim_thread_id)
            except Exception:  # noqa: BLE001
                logger.warning("Slack event claim failed for key=%s", claim_key)
                return True
            _claim_locally(claim_key)
            _claim_locally(event_id)
            return False

        _claim_locally(claim_key)
        _claim_locally(event_id)
        return True
