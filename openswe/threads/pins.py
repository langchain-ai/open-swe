"""The threads each person pinned to the top of their dashboard sidebar."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from openswe.database import postgres
from openswe.users.records import UserRecord, UserRecords, user_id_for_login


class ThreadPins(UserRecords):
    async def reorder(self, login: str, thread_ids: list[str]) -> None:
        async with postgres.session() as session:
            records = list(
                await session.scalars(
                    select(UserRecord)
                    .where(
                        UserRecord.user_id == user_id_for_login(login), UserRecord.kind == self.kind
                    )
                    .with_for_update()
                )
            )
            by_id = {record.key: record for record in records}
            if len(thread_ids) != len(set(thread_ids)) or not set(thread_ids).issubset(by_id):
                raise ValueError("Only your pinned chats can be reordered")
            ordered = [by_id[key] for key in thread_ids]
            ordered.extend(
                sorted(
                    (record for record in records if record.key not in thread_ids),
                    key=lambda record: (
                        record.updated_at or datetime.min.replace(tzinfo=UTC),
                        record.key,
                    ),
                )
            )
            now = datetime.now(UTC)
            for index, record in enumerate(ordered):
                record.updated_at = now + timedelta(microseconds=index)


THREAD_PINS = ThreadPins("thread_pin")


async def list_thread_pin_ids(login: str) -> list[str]:
    """The person's pinned thread IDs, oldest pin first."""
    return await THREAD_PINS.keys(login)


async def pin_thread(login: str, thread_id: str) -> None:
    await THREAD_PINS.add(login, {}, key=thread_id)


async def unpin_thread(login: str, thread_id: str) -> None:
    await THREAD_PINS.delete(login, key=thread_id)
