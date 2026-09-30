"""Thread metadata for running sandbox commands."""

from collections.abc import Sequence

from langgraph_sdk.client import LangGraphClient

from agent.utils.json_types import thread_metadata
from agent.utils.thread_pr_state import agent_thread_pr_state_lock

RUNNING_BACKGROUND_TASKS_KEY = "running_background_tasks"


async def update_background_task_state(
    client: LangGraphClient,
    thread_id: str,
    *,
    running: Sequence[str] = (),
    finished: Sequence[str] = (),
    reset: bool = False,
) -> dict[str, object]:
    """Merge transitions under the lock and return the resulting metadata snapshot."""
    async with agent_thread_pr_state_lock(client, thread_id):
        metadata = thread_metadata(await client.threads.get(thread_id))
        previous = metadata.get(RUNNING_BACKGROUND_TASKS_KEY)
        active = (
            {item for item in previous if isinstance(item, str)}
            if isinstance(previous, list)
            else set()
        )
        active = set() if reset else (active | set(running)) - set(finished)
        current = sorted(active)
        if previous != current and not (previous is None and not current):
            await client.threads.update(
                thread_id,
                metadata={RUNNING_BACKGROUND_TASKS_KEY: current},
            )
        return {**metadata, RUNNING_BACKGROUND_TASKS_KEY: current}
