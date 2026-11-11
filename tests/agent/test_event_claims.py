import asyncio
from datetime import timedelta

from sqlalchemy import text

from openswe import event_claims
from openswe.database.postgres import transaction
from openswe.jobs import queue


async def _keys() -> set[str]:
    async with transaction() as conn:
        rows = await conn.execute(text("SELECT key FROM event_claim WHERE scope = 'test'"))
        return {row[0] for row in rows}


async def test_a_claim_is_taken_once_and_released_claims_are_taken_again(registry_db) -> None:  # noqa: ANN001, ARG001
    assert await event_claims.claim("test", "delivery", ttl=timedelta(hours=1))
    assert not await event_claims.claim("test", "delivery", ttl=timedelta(hours=1))

    await event_claims.release("test", "delivery")
    assert await event_claims.claim("test", "delivery", ttl=timedelta(hours=1))


async def test_a_lapsed_claim_is_taken_again_and_purged_only_once_lapsed(registry_db) -> None:  # noqa: ANN001, ARG001
    assert await event_claims.claim("test", "lapsed", ttl=timedelta(seconds=-1))
    assert not await event_claims.held("test", "lapsed")
    assert await event_claims.claim("test", "retaken", ttl=timedelta(seconds=-1))
    assert await event_claims.claim("test", "retaken", ttl=timedelta(hours=1))
    assert await event_claims.claim("test", "live", ttl=timedelta(hours=1))

    job = await queue.schedule(event_claims.PURGE_JOB)
    await event_claims.purge_lapsed(job)
    assert await _keys() == {"retaken", "live"}


async def test_the_purge_finds_lapsed_claims_through_the_expiry_index(registry_db: None) -> None:
    async with transaction() as conn:
        await conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await conn.execute(text(f"EXPLAIN {event_claims._PURGE.text}"), {"limit": 100})
        assert "event_claim_expires_idx" in "\n".join(row[0] for row in plan)


async def test_concurrent_claims_never_exceed_the_limit(registry_db) -> None:  # noqa: ANN001, ARG001
    outcomes = await asyncio.gather(
        *(
            event_claims.claim_within_limit(
                "test",
                f"burst:{index}",
                ttl=timedelta(hours=1),
                group="burst:",
                limit=2,
                within=timedelta(hours=1),
            )
            for index in range(5)
        )
    )

    assert sorted(outcomes) == ["claimed", "claimed", "limited", "limited", "limited"]


async def test_cooldown_extends_never_shortens_and_expires(registry_db: None) -> None:
    assert await event_claims.remaining("test", "cooldown") == 0
    await asyncio.gather(
        event_claims.defer("test", "cooldown", ttl=timedelta(seconds=30)),
        event_claims.defer("test", "cooldown", ttl=timedelta(seconds=60)),
    )
    await event_claims.defer("test", "cooldown", ttl=timedelta(seconds=1))
    assert 50 < await event_claims.remaining("test", "cooldown") <= 60
    async with transaction() as conn:
        await conn.execute(text("UPDATE event_claim SET expires_at = clock_timestamp()"))
    assert await event_claims.remaining("test", "cooldown") == 0
