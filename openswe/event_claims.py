"""Exactly-once claims for inbound events.

A webhook can arrive more than once: the provider retries a delivery it thinks
failed, and an admin can redeliver one by hand. A handler claims
``(scope, key)`` before acting, and only the first claim wins. A handler that
fails before its side effect releases the claim, so a later delivery of the
same event (a provider retry or a manual redelivery) runs.
A claim lapses at ``expires_at`` and can then be taken again. A lapsed row is
inert: every read checks ``expires_at``. The ``event_claim_purge`` job deletes
lapsed rows every few minutes, so taking a claim never pays for cleanup and
the table stays about as large as the live claims.
Short-lived mutexes use the same claims, so one whose holder never releases it
lapses on its own.
"""

import logging
from datetime import timedelta
from typing import Final, Literal

from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.jobs.queue import Job

logger = logging.getLogger(__name__)

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
_PURGE = text(
    """
    DELETE FROM event_claim WHERE (scope, key) IN (
        SELECT scope, key FROM event_claim
        -- statement_timestamp() is STABLE, so the expiry index bounds the scan;
        -- the VOLATILE clock_timestamp() would scan the whole table.
        WHERE expires_at <= statement_timestamp()
        ORDER BY expires_at
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    )
    """
)
PURGE_JOB: Final = "event_claim_purge"
PURGE_SCHEDULE: Final = "*/5 * * * *"
# Small enough that each delete is a short transaction and a short burst of WAL.
_PURGE_BATCH: Final = 5_000


async def claim(scope: str, key: str, *, ttl: timedelta) -> bool:
    """Take the claim on ``(scope, key)``; ``False`` when someone already holds it."""
    async with transaction() as conn:
        result = await conn.execute(_CLAIM, {"scope": scope, "key": key, "ttl": ttl})
        return result.first() is not None


async def held(scope: str, key: str) -> bool:
    """Whether someone holds the claim on ``(scope, key)``, for handlers that claim once done."""
    async with transaction() as conn:
        result = await conn.execute(
            text(
                "SELECT 1 FROM event_claim WHERE scope = :scope AND key = :key "
                "AND expires_at > clock_timestamp()"
            ),
            {"scope": scope, "key": key},
        )
        return result.first() is not None


async def remaining(scope: str, key: str) -> float:
    """Seconds until a claim expires, or zero when it is absent or expired."""
    async with transaction() as conn:
        result = await conn.execute(
            text(
                "SELECT greatest(0, extract(epoch FROM expires_at - clock_timestamp())) "
                "FROM event_claim WHERE scope = :scope AND key = :key"
            ),
            {"scope": scope, "key": key},
        )
        return float(result.scalar_one_or_none() or 0)


async def defer(scope: str, key: str, *, ttl: timedelta) -> None:
    """Keep a cooldown claim until at least ``ttl`` from now, never shortening it."""
    async with transaction() as conn:
        await conn.execute(
            text(
                "INSERT INTO event_claim (scope, key, expires_at) "
                "VALUES (:scope, :key, clock_timestamp() + CAST(:ttl AS interval)) "
                "ON CONFLICT (scope, key) DO UPDATE "
                "SET expires_at = greatest(event_claim.expires_at, EXCLUDED.expires_at)"
            ),
            {"scope": scope, "key": key, "ttl": ttl},
        )


async def release(scope: str, key: str) -> None:
    """Give a claim back, so the event's next delivery is handled."""
    async with transaction() as conn:
        await conn.execute(
            text("DELETE FROM event_claim WHERE scope = :scope AND key = :key"),
            {"scope": scope, "key": key},
        )


ClaimOutcome = Literal["claimed", "taken", "limited"]


async def claim_within_limit(
    scope: str, key: str, *, ttl: timedelta, group: str, limit: int, within: timedelta
) -> ClaimOutcome:
    """Claim ``(scope, key)`` unless ``limit`` claims whose keys start with ``group``
    were taken in the last ``within``.

    The count and the claim share one transaction under a lock on ``group``, so
    a burst of deliveries can't all see room under the limit.
    """
    async with transaction() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock))"), {"lock": f"{scope}:{group}"}
        )
        recent = await conn.execute(
            text(
                "SELECT count(*) FROM event_claim WHERE scope = :scope "
                "AND left(key, length(:group)) = :group "
                "AND claimed_at > clock_timestamp() - CAST(:within AS interval)"
            ),
            {"scope": scope, "group": group, "within": within},
        )
        if int(recent.scalar_one()) >= limit:
            return "limited"
        result = await conn.execute(_CLAIM, {"scope": scope, "key": key, "ttl": ttl})
        return "claimed" if result.first() is not None else "taken"


async def purge_lapsed(_job: Job) -> None:
    """The ``event_claim_purge`` job: delete lapsed claims.

    Deletes in batches, each its own transaction, so a backlog never holds one
    long transaction. ``SKIP LOCKED`` leaves rows a claim is retaking to it, and
    lets two overlapping purges split the work.
    """
    purged = 0
    while True:
        async with transaction() as conn:
            result = await conn.execute(_PURGE, {"limit": _PURGE_BATCH})
        purged += result.rowcount
        if result.rowcount < _PURGE_BATCH:
            break
    if purged:
        logger.info("Lapsed event claims purged", extra={"purged": purged})
