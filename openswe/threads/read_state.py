"""Each person's read position in a thread, independent of its shared metadata."""

from openswe.users.records import UserRecords
from openswe.utils.json_types import JsonObject


class ThreadReadStates(UserRecords):
    def __init__(self) -> None:
        super().__init__("thread_read_state")

    async def saved(self, login: str, thread_id: str) -> JsonObject | None:
        return await self.get(login, key=thread_id)

    async def for_threads(self, login: str, thread_ids: list[str]) -> dict[str, JsonObject]:
        return await self.for_keys(login, thread_ids)

    async def set(
        self,
        login: str,
        thread_id: str,
        *,
        viewed: bool,
        run_id: str | None,
        viewed_at_ms: int,
    ) -> None:
        await self.put(
            login,
            {
                "viewed": viewed,
                "last_viewed_run_id": run_id if viewed else None,
                "last_viewed_at_ms": viewed_at_ms if viewed else None,
            },
            key=thread_id,
        )


THREAD_READ_STATES = ThreadReadStates()
