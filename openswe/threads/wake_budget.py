"""Runs a thread may start for itself between the messages people send it."""

from typing import Any

from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, JsonValue

from openswe.input_messages import latest_human_message_key

THREAD_WAKEUP_GENERATION_KEY = "thread_wakeup_generation"
THREAD_WAKEUP_COUNT_KEY = "thread_wakeup_count"


class _ThreadValues(BaseModel):
    messages: list[JsonValue] = []


class _ThreadState(BaseModel):
    values: _ThreadValues | None = None


async def latest_human_message(client: LangGraphClient, thread_id: str) -> str:
    """The key of the newest human message in ``thread_id``'s saved state."""
    state = _ThreadState.model_validate(await client.threads.get_state(thread_id))
    return latest_human_message_key(state.values.messages if state.values else [])


class WakeBudget:
    """Runs a thread may start for itself between human messages, counted in its metadata.

    The count resets whenever a new human message lands in the thread.
    """

    def __init__(self, *, generation_key: str, count_key: str, limit: int) -> None:
        self.limit = limit
        self._generation_key = generation_key
        self._count_key = count_key

    async def exhausted(self, client: Any, thread_id: str) -> bool:
        _, count = await self.used(client, thread_id)
        return count >= self.limit

    async def spend(self, client: Any, thread_id: str) -> bool:
        """Count one more run; ``False``, counting nothing, once the limit is reached."""
        generation, count = await self.used(client, thread_id)
        if count >= self.limit:
            return False
        await self.record(client, thread_id, generation, count + 1)
        return True

    async def used(self, client: Any, thread_id: str) -> tuple[str, int]:
        """The current human-message generation and how many runs it has spent."""
        generation = await latest_human_message(client, thread_id)

        thread = await client.threads.get(thread_id)
        metadata = thread.get("metadata") if isinstance(thread, dict) else None
        metadata = metadata if isinstance(metadata, dict) else {}
        if metadata.get(self._generation_key) != generation:
            return generation, 0
        count = metadata.get(self._count_key)
        return generation, count if isinstance(count, int) and count >= 0 else 0

    async def record(self, client: Any, thread_id: str, generation: str, count: int) -> None:
        await client.threads.update(
            thread_id=thread_id,
            metadata={self._generation_key: generation, self._count_key: count},
        )


THREAD_WAKEUPS = WakeBudget(
    generation_key=THREAD_WAKEUP_GENERATION_KEY, count_key=THREAD_WAKEUP_COUNT_KEY, limit=10
)
"""Runs ``schedule_thread_wakeup`` may schedule."""

MONITOR_WAKES = WakeBudget(
    generation_key="monitor_wake_generation", count_key="monitor_wake_count", limit=10
)
"""Runs started only to hand a thread what its sandbox monitors emitted."""
