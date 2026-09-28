"""The only place LangGraph threads are created, so every thread a person can open has a title."""

from collections.abc import Mapping
from typing import Literal

from langgraph_sdk.client import LangGraphClient

from agent.utils.json_types import thread_metadata


def _require_title(title: str) -> str:
    if not title.strip():
        raise ValueError("a thread needs a title")
    return title


async def create_thread(
    client: LangGraphClient,
    thread_id: str,
    *,
    title: str,
    if_exists: Literal["raise", "do_nothing"],
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Create a thread people can open; with ``do_nothing`` an existing thread keeps its metadata."""
    await client.threads.create(
        thread_id=thread_id,
        if_exists=if_exists,
        metadata={**(metadata or {}), "title": _require_title(title)},
    )


async def ensure_titled_thread(client: LangGraphClient, thread_id: str, *, title: str) -> None:
    """Create a system-named thread if needed and keep its title current."""
    thread = await client.threads.create(
        thread_id=thread_id, if_exists="do_nothing", metadata={"title": _require_title(title)}
    )
    if thread_metadata(thread).get("title") != title:
        await client.threads.update(thread_id=thread_id, metadata={"title": title})


async def create_lock_thread(
    client: LangGraphClient,
    thread_id: str,
    *,
    ttl_minutes: int,
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Hold a short-lived, never-listed thread as a mutex; raises ``ConflictError`` while held."""
    if metadata:
        await client.threads.create(
            thread_id=thread_id, if_exists="raise", ttl=ttl_minutes, metadata=dict(metadata)
        )
    else:
        await client.threads.create(thread_id=thread_id, if_exists="raise", ttl=ttl_minutes)
