import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.database import postgres
from openswe.users.models import User
from openswe.users.records import UserRecord, UserRecords
from openswe.utils.json_types import JsonObject


async def test_read_only_transaction_rejects_writes(registry_db: None) -> None:
    async with postgres.read_only_transaction() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
        with pytest.raises(DBAPIError, match="read-only transaction"):
            await conn.execute(text("CREATE TABLE forbidden_write (id INTEGER)"))


async def test_untrusted_read_only_transaction_discards_session_state(
    registry_db: None,
) -> None:
    lock_key = 815_196_729
    async with postgres.untrusted_read_only_transaction() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})

    async with postgres.connection() as conn:
        acquired = await conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key})
        assert acquired is True
        await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})


async def test_borrowed_session_keeps_reads_and_writes_in_the_callers_transaction(
    registry_db: None,
) -> None:
    person = User()
    records = UserRecords("session_reuse")
    async with postgres.session() as outer:
        outer.add(person)
        await outer.flush()
        outer.add(UserRecord(user_id=person.id, kind=records.kind, key="pin", value={"rank": 1}))
        await outer.flush()
        assert await records.keys(f"user:{person.id}", session=outer) == ["pin"]
        assert await records.get(f"user:{person.id}", "pin", session=outer) == {"rank": 1}
        async with postgres.session(outer) as borrowed:
            await borrowed.execute(text("SELECT 1"))
        await outer.rollback()
    assert await User.get(person.id) is None


async def test_optional_preferences_leave_a_shared_transaction_usable(
    registry_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.dashboard import user_preferences

    class UnreadablePreferences(UserRecords):
        async def get(
            self, login: str, key: str = "", *, session: AsyncSession | None = None
        ) -> JsonObject | None:
            assert session is not None
            await session.execute(text("SELECT 1 / 0"))
            return None

    monkeypatch.setattr(user_preferences, "USER_PREFERENCES", UnreadablePreferences("preferences"))
    async with postgres.session() as session:
        preferences = await user_preferences.get_user_preferences("alice", session=session)
        assert preferences["default_visibility"] == "private"
        assert await session.scalar(text("SELECT 1")) == 1
