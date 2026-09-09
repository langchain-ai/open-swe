"""Deduplication of Slack Event API deliveries."""

from agent.webhooks.claims import DeliveryClaims

_claims = DeliveryClaims(("slack-event",))


def slack_message_claim_key(event_id: str, channel_id: str = "", event_ts: str = "") -> str:
    """Key a claim on the message itself, not the delivery."""
    if channel_id and event_ts:
        return f"{channel_id}:{event_ts}"
    return event_id


def slack_claim_thread_id(claim_key: str) -> str:
    return _claims.thread_id(claim_key)


def reset_slack_event_claims() -> None:
    _claims.reset()


async def slack_event_already_seen(event_id: str) -> bool:
    """Check whether this process has already claimed the event."""
    return _claims.seen(event_id)


async def claim_slack_event(event_id: str, channel_id: str = "", event_ts: str = "") -> bool:
    """Atomically claim a Slack message; fail open when the platform is unavailable."""
    return await _claims.claim(event_id, slack_message_claim_key(event_id, channel_id, event_ts))
