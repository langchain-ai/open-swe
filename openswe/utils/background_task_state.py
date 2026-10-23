"""Thread metadata for running sandbox commands and monitors."""

from collections.abc import Sequence
from typing import Literal

from langgraph_sdk.client import LangGraphClient

from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_pr_state import agent_thread_pr_state_lock

RUNNING_BACKGROUND_TASKS_KEY = "running_background_tasks"
RUNNING_MONITORS_KEY = "running_monitors"

type RunningWorkKey = Literal["running_background_tasks", "running_monitors"]


async def update_background_task_state(
    client: LangGraphClient,
    thread_id: str,
    *,
    running: Sequence[str] = (),
    finished: Sequence[str] = (),
    reset: bool = False,
    key: RunningWorkKey = RUNNING_BACKGROUND_TASKS_KEY,
) -> dict[str, object]:
    """Merge transitions into ``key`` under the lock and return the resulting metadata."""
    from openswe.ui_invalidations.topics import Topic

    async with agent_thread_pr_state_lock(thread_id):
        metadata = thread_metadata(await client.threads.get(thread_id))
        previous = metadata.get(key)
        active = (
            {item for item in previous if isinstance(item, str)}
            if isinstance(previous, list)
            else set()
        )
        active = set() if reset else (active | set(running)) - set(finished)
        current = sorted(active)
        if previous != current and not (previous is None and not current):
            await client.threads.update(thread_id, metadata={key: current})
            await Topic.THREAD_BACKGROUND_WORK.invalidate(key=thread_id)
        return {**metadata, key: current}
