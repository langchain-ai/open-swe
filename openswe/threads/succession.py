"""Carrying a thread's work over from before Managed Deep Agents.

A ``local`` thread takes no new activity. Work whose state lives on its thread, such
as a pull request's reviewer or an incident, continues on the thread's successor,
which starts with its metadata. The predecessor stays readable and records its
successor, so listings can show one thread per piece of work.
"""

from typing import Final

from openswe.agent_api.client import RUNTIME_METADATA_KEY
from openswe.threads import index
from openswe.utils.thread_ops import langgraph_client

SUCCEEDED_BY_KEY: Final = "succeeded_by"
# Point at runs on the predecessor, which the successor has none of.
_RUN_KEYS: Final = frozenset({"latest_run_id", "latest_run_status", "current_reviewer_run_id"})


async def succeed(thread_id: str) -> str:
    """The thread to start new work for ``thread_id`` on, creating its successor if needed."""
    successor = await index.live_thread_id(thread_id)
    if successor == thread_id:
        return thread_id
    if await index.get(successor) is None and (thread := await index.get(thread_id)) is not None:
        client = langgraph_client()
        await client.threads.create(
            thread_id=successor,
            metadata={
                key: value
                for key, value in thread.metadata.items()
                if key not in _RUN_KEYS and key != RUNTIME_METADATA_KEY
            },
            if_exists="do_nothing",
        )
        await client.threads.update(thread_id, metadata={SUCCEEDED_BY_KEY: successor})
    return successor
