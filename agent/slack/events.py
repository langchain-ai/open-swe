"""Deduplication of Slack Event API deliveries."""

import asyncio
import logging
import time
import uuid
from collections import OrderedDict

from fastapi import HTTPException
from langgraph_sdk import get_client

from agent.config import ENV
from agent.prompts import load_prompt

logger = logging.getLogger(__name__)

LANGGRAPH_URL = ENV.LANGGRAPH_URL.get()

_LOCAL_CLAIM_LIMIT = 2048
_CLAIM_TTL_SECONDS = 600
_CLAIM_TIMEOUT_SECONDS = 5
_claimed_keys: OrderedDict[str, float] = OrderedDict()
_claim_lock = asyncio.Lock()


class SlackClaimUnavailable(HTTPException):
    """The webhook must stay unacknowledged until shared deduplication recovers."""


def _claim_thread_id(claim_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:slack-event:{claim_key}"))


def slack_message_claim_key(event_id: str, channel_id: str = "", event_ts: str = "") -> str:
    """Key a claim on the message itself, not the delivery."""
    if channel_id and event_ts:
        return f"{channel_id}:{event_ts}"
    return event_id


async def _claim_remotely(claim_key: str) -> bool:
    client = get_client(url=LANGGRAPH_URL)
    claim_thread_id = _claim_thread_id(claim_key)
    token = uuid.uuid4().hex
    try:
        async with asyncio.timeout(_CLAIM_TIMEOUT_SECONDS):
            try:
                await client.threads.create(
                    thread_id=claim_thread_id,
                    if_exists="raise",
                    ttl=_CLAIM_TTL_SECONDS // 60,
                    metadata={"slack_claim_token": token},
                )
            except Exception:
                logger.debug("Slack claim write failed; checking ownership", exc_info=True)
                existing = await client.threads.get(claim_thread_id)
                metadata = existing.get("metadata")
                return isinstance(metadata, dict) and metadata.get("slack_claim_token") == token
    except Exception as exc:
        logger.warning(
            "Slack claim storage unavailable", extra={"claim_key": claim_key}, exc_info=True
        )
        raise SlackClaimUnavailable(
            status_code=503,
            detail=load_prompt("slack/claim-unavailable.md"),
            headers={"Retry-After": "1"},
        ) from exc
    return True


def _claim_locally(claim_key: str) -> None:
    if not claim_key:
        return
    _claimed_keys[claim_key] = time.monotonic() + _CLAIM_TTL_SECONDS
    _claimed_keys.move_to_end(claim_key)
    while len(_claimed_keys) > _LOCAL_CLAIM_LIMIT:
        _claimed_keys.popitem(last=False)


def reset_slack_event_claims() -> None:
    _claimed_keys.clear()


async def slack_event_already_seen(event_id: str) -> bool:
    """Check whether this process has already claimed the event."""
    return _seen_locally(event_id)


def _seen_locally(key: str) -> bool:
    expiry = _claimed_keys.get(key)
    if expiry is None:
        return False
    if expiry <= time.monotonic():
        del _claimed_keys[key]
        return False
    return True


async def claim_slack_event(event_id: str, channel_id: str = "", event_ts: str = "") -> bool:
    """Claim an event across workers; unavailable storage requests a delivery retry."""
    claim_key = slack_message_claim_key(event_id, channel_id, event_ts)
    if not claim_key:
        return True

    async with _claim_lock:
        if _seen_locally(claim_key) or _seen_locally(event_id):
            return False

    # Remote uniqueness arbitrates concurrent workers. Do not hold the process-wide
    # cache lock while storage is slow: unrelated events must remain independent.
    if not await _claim_remotely(claim_key):
        async with _claim_lock:
            _claim_locally(claim_key)
            _claim_locally(event_id)
        return False
    async with _claim_lock:
        _claim_locally(claim_key)
        _claim_locally(event_id)
    return True
