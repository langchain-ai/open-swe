"""The threads each person pinned to the top of their dashboard sidebar."""

from openswe.users.records import UserRecords


class ThreadPins(UserRecords):
    def __init__(self) -> None:
        super().__init__("thread_pin")
        self.order = UserRecords("thread_pin_order")

    async def ordered_ids(self, login: str) -> list[str]:
        pin_ids = await self.keys(login)
        order = await self.order.get(login)
        saved = order.get("thread_ids") if order else None
        pinned = set(pin_ids)
        ordered = dict.fromkeys(
            thread_id
            for thread_id in (saved if isinstance(saved, list) else [])
            if isinstance(thread_id, str) and thread_id in pinned
        )
        return [*ordered, *(thread_id for thread_id in pin_ids if thread_id not in ordered)]

    async def reorder(self, login: str, thread_ids: list[str]) -> None:
        if len(thread_ids) > 1000:
            raise ValueError("Reorder at most 1000 threads at a time")
        if len(thread_ids) != len(set(thread_ids)):
            raise ValueError("thread_ids must not contain duplicates")
        current = await self.ordered_ids(login)
        if not set(thread_ids).issubset(current):
            raise ValueError("Only your already pinned threads can be reordered")
        selected = set(thread_ids)
        await self.order.put(
            login,
            {"thread_ids": [*thread_ids, *(pin for pin in current if pin not in selected)]},
        )

    async def unpin(self, login: str, thread_id: str) -> None:
        await self.delete(login, key=thread_id)
        order = await self.order.get(login)
        if order:
            await self.order.put(login, {"thread_ids": await self.ordered_ids(login)})


THREAD_PINS = ThreadPins()


async def list_thread_pin_ids(login: str) -> list[str]:
    """The person's pinned thread IDs, explicitly ordered or oldest pin first."""
    return await THREAD_PINS.ordered_ids(login)


async def pin_thread(login: str, thread_id: str) -> None:
    await THREAD_PINS.add(login, {}, key=thread_id)


async def unpin_thread(login: str, thread_id: str) -> None:
    await THREAD_PINS.unpin(login, thread_id)
