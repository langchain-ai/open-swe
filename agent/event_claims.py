"""Exactly-once claims for inbound events.

A webhook can arrive more than once: the provider retries a delivery it thinks
failed, and an admin can redeliver one by hand. A handler claims
``(scope, key)`` before acting, and only the first claim wins. A handler that
fails before its side effect releases the claim, so a later delivery of the
same event (a provider retry or a manual redelivery) runs.
A claim lapses at ``expires_at`` and can then be taken again. Each claim also
sweeps a few lapsed ones, so the table stays about as large as the live claims.
"""

from datetime import timedelta

from sqlalchemy import text

from agent.database.postgres import transaction

_CLAIM = text(
    """
    INSERT INTO event_claim (scope, key, expires_at)
    VALUES (:scope, :key, clock_timestamp() + CAST(:ttl AS interval))
    ON CONFLICT (scope, key) DO UPDATE
        SET claimed_at = clock_timestamp(), expires_at = clock_timestamp() + CAST(:ttl AS interval)
        WHERE event_claim.expires_at <= clock_timestamp()
    RETURNING 1
    """
)
_SWEEP = text(
    """
    DELETE FROM event_claim WHERE (scope, key) IN (
        SELECT scope, key FROM event_claim
        WHERE expires_at <= clock_timestamp()
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    )
    """
)
_SWEEP_LIMIT = 100


async def claim(scope: str, key: str, *, ttl: timedelta) -> bool:
    """Take the claim on ``(scope, key)``; ``False`` when someone already holds it."""
    async with transaction() as conn:
        await conn.execute(_SWEEP, {"limit": _SWEEP_LIMIT})
        result = await conn.execute(_CLAIM, {"scope": scope, "key": key, "ttl": ttl})
        return result.first() is not None


async def release(scope: str, key: str) -> None:
    """Give a claim back, so the event's next delivery is handled."""
    async with transaction() as conn:
        await conn.execute(
            text("DELETE FROM event_claim WHERE scope = :scope AND key = :key"),
            {"scope": scope, "key": key},
        )


async def count_recent(scope: str, key_prefix: str, *, within: timedelta) -> int:
    """How many claims in ``scope`` whose key starts with ``key_prefix`` were taken lately."""
    async with transaction() as conn:
        result = await conn.execute(
            text(
                "SELECT count(*) FROM event_claim WHERE scope = :scope "
                "AND left(key, length(:prefix)) = :prefix "
                "AND claimed_at > clock_timestamp() - CAST(:within AS interval)"
            ),
            {"scope": scope, "prefix": key_prefix, "within": within},
        )
        return int(result.scalar_one())
