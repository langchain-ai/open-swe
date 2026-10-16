"""The threads each person pinned to the top of their dashboard sidebar."""

from openswe.users.records import UserRecords

THREAD_PINS = UserRecords("thread_pin")


async def list_thread_pin_ids(login: str) -> list[str]:
    """The person's pinned thread IDs, oldest pin first."""
    return await THREAD_PINS.keys(login)


async def pin_thread(login: str, thread_id: str) -> None:
    await THREAD_PINS.add(login, {}, key=thread_id)


async def unpin_thread(login: str, thread_id: str) -> None:
    await THREAD_PINS.delete(login, key=thread_id)
