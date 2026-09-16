import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agent.database import postgres


async def test_read_only_transaction_rejects_writes(registry_db: None) -> None:
    async with postgres.read_only_transaction() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
        with pytest.raises(DBAPIError, match="read-only transaction"):
            await conn.execute(text("CREATE TABLE forbidden_write (id INTEGER)"))


async def test_read_only_transaction_rolls_back_and_invalidates_connection(
    registry_db: None,
) -> None:
    lock_key = 815_196_729
    async with postgres.read_only_transaction() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})

    async with postgres.connection() as conn:
        acquired = await conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key})
        assert acquired is True
        await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
