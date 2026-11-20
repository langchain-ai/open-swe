"""Carry legacy work and deleted-sandbox reviews onto fresh threads.

A ``local`` thread takes no new activity. Work whose state lives on its thread, such
as a pull request's reviewer or an incident, continues on the thread's successor,
which starts with its metadata. The predecessor stays readable and records its
successor, so listings can show one thread per piece of work.
"""

from typing import Final

from openswe.agent_api.client import RUNTIME_METADATA_KEY
from openswe.tasks.schemas import ThreadMetadata
from openswe.thread_ids import mda_successor_thread_id
from openswe.threads import index
from openswe.threads.store import THREADS

SUCCEEDED_BY_KEY: Final = "succeeded_by"
# Point at runs on another thread, which a successor or a copy has none of.
RUN_KEYS: Final = frozenset({"latest_run_id", "latest_run_status", "current_reviewer_run_id"})


async def succeed(thread_id: str, *, restart_deleted: bool = False) -> str:
    """The thread to start new work for ``thread_id`` on, creating its successor if needed."""
    thread_id = await index.current_thread_id(thread_id)
    successor = await index.live_thread_id(thread_id)
    if successor == thread_id:
        thread = await index.get(thread_id) if restart_deleted else None
        if (
            thread is None
            or thread.metadata.get("kind") != "reviewer"
            or not await ThreadMetadata.model_validate(thread.metadata).mda_sandbox_deleted()
        ):
            return thread_id
        successor = mda_successor_thread_id(thread_id)
    if (thread := await index.get(thread_id)) is None:
        return successor
    if await index.get(successor) is None:
        title = thread.metadata.get("title")
        await THREADS.create(
            successor,
            title=title if isinstance(title, str) and title else "Continued thread",
            metadata={
                key: value
                for key, value in thread.metadata.items()
                if key not in RUN_KEYS
                and key not in {RUNTIME_METADATA_KEY, SUCCEEDED_BY_KEY, "running_background_tasks"}
                and not key.startswith("sandbox_")
            },
        )
    await THREADS.update(thread_id, metadata={SUCCEEDED_BY_KEY: successor})
    return successor
