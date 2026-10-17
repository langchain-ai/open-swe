"""Runs a thread may start for itself between the messages people send it."""

import hashlib
from typing import Any
from xml.etree import ElementTree

from langchain_core.messages import BaseMessage

THREAD_WAKEUP_GENERATION_KEY = "thread_wakeup_generation"
THREAD_WAKEUP_COUNT_KEY = "thread_wakeup_count"


def _message_content(message: object) -> tuple[object, str | None]:
    if isinstance(message, BaseMessage):
        return message.content, message.id
    if isinstance(message, dict):
        message_id = message.get("id")
        return message.get("content"), message_id if isinstance(message_id, str) else None
    return None, None


def _input_message_kind(content: object) -> str | None:
    values = content if isinstance(content, list) else [content]
    for value in values:
        text = value.get("text") if isinstance(value, dict) else value
        if not isinstance(text, str) or "<input-message" not in text:
            continue
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            continue
        messages = [root] if root.tag == "input-message" else root.findall(".//input-message")
        for message in messages:
            kind = message.get("kind")
            if kind in {"human", "system"}:
                return kind
    return None


def latest_human_generation(messages: object) -> str:
    if not isinstance(messages, (list, tuple)):
        return "no-human-message"
    for index in range(len(messages) - 1, -1, -1):
        content, message_id = _message_content(messages[index])
        if _input_message_kind(content) != "human":
            continue
        identity = message_id or repr(content)
        return hashlib.sha256(f"{index}:{identity}".encode()).hexdigest()
    return "no-human-message"


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
        state = await client.threads.get_state(thread_id)
        values = state.get("values") if isinstance(state, dict) else None
        messages = values.get("messages") if isinstance(values, dict) else None
        generation = latest_human_generation(messages)

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
