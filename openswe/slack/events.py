"""Deduplication of Slack Event API deliveries."""

import asyncio
import logging
from collections import OrderedDict
from datetime import timedelta

from openswe import event_claims
from openswe.config import ENV

logger = logging.getLogger(__name__)

LANGGRAPH_URL = ENV.LANGGRAPH_URL.get()

_LOCAL_CLAIM_LIMIT = 2048
_CLAIM_SCOPE = "slack_event"
_CLAIM_TTL = timedelta(minutes=10)
_claimed_keys: OrderedDict[str, None] = OrderedDict()
_claim_lock = asyncio.Lock()


def slack_message_claim_key(event_id: str, channel_id: str = "", event_ts: str = "") -> str:
    """Key a claim on the message itself, not the delivery."""
    if channel_id and event_ts:
        return f"{channel_id}:{event_ts}"
    return event_id


async def _claim_remotely(claim_key: str) -> bool | None:
    try:
        return await event_claims.claim(_CLAIM_SCOPE, claim_key, ttl=_CLAIM_TTL)
    except Exception:
        logger.warning("Slack event claim failed for key=%s", claim_key, exc_info=True)
        return None


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

        claimed = await _claim_remotely(event_id)
        if claimed is False:
            _claim_locally(event_id)
            return False
        if claim_key != event_id:
            message_claimed = await _claim_remotely(claim_key)
            if message_claimed is False:
                _claim_locally(claim_key)
                _claim_locally(event_id)
                return False
            claimed = claimed or message_claimed

        if claimed:
            _claim_locally(claim_key)
            _claim_locally(event_id)
        return True
