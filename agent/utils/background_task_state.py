"""Thread metadata for running sandbox commands."""

from collections.abc import Sequence

from langgraph_sdk.client import LangGraphClient

from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import read_thread_fields
from agent.utils.thread_pr_state import agent_thread_pr_state_lock

RUNNING_BACKGROUND_TASKS_KEY = "running_background_tasks"


async def update_background_task_state(
    client: LangGraphClient,
    thread_id: str,
    *,
    running: Sequence[str] = (),
    finished: Sequence[str] = (),
    reset: bool = False,
) -> None:
    """Merge observed task transitions without dropping concurrent launches."""
    async with agent_thread_pr_state_lock(client, thread_id):
        metadata = thread_metadata(await read_thread_fields(client, thread_id, ["metadata"]))
        previous = metadata.get(RUNNING_BACKGROUND_TASKS_KEY)
        active = (
            {item for item in previous if isinstance(item, str)}
            if isinstance(previous, list)
            else set()
        )
        active = set() if reset else (active | set(running)) - set(finished)
        updated = sorted(active)
        if previous == updated:
            return
        await client.threads.update(
            thread_id,
            metadata={RUNNING_BACKGROUND_TASKS_KEY: updated},
            return_minimal=True,
        )
