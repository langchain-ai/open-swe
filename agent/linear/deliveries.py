"""Freshness and once-only handling of Linear webhook deliveries."""

from datetime import UTC, datetime

from agent.webhooks.claims import DeliveryClaims

_claims = DeliveryClaims(("linear", "deliveries"))


def is_fresh(
    webhook_timestamp_ms: int, *, now: datetime | None = None, max_age_seconds: int = 60
) -> bool:
    """Whether a Linear ``webhookTimestamp`` falls inside the replay window."""
    reference = now or datetime.now(UTC)
    age_seconds = reference.timestamp() - webhook_timestamp_ms / 1000
    return 0 <= age_seconds <= max_age_seconds


async def claim_delivery(delivery_id: str) -> bool:
    """Claim a ``Linear-Delivery`` id; fail open when the platform is unavailable."""
    return await _claims.claim(delivery_id)


def reset_delivery_claims() -> None:
    _claims.reset()
