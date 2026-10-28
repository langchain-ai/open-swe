"""Record that this thread should be checked after its pull request deploys."""

import logging

from pydantic import JsonValue

from openswe.rollout_events import (
    ROLLOUT_CHECK_REQUESTED,
    merged_pull_request,
    subscribe_merged_thread,
)
from openswe.run_config import RunConfig
from openswe.threads import index
from openswe.threads.index import IndexedThread
from openswe.threads.runtimes import RUNTIMES

logger = logging.getLogger(__name__)

_FLAG: dict[str, JsonValue] = {ROLLOUT_CHECK_REQUESTED: True}


async def _copy_flag(thread: IndexedThread) -> None:
    """Copy the flag onto every runtime that keeps this thread. A failed copy stays in the index."""
    for runtime in RUNTIMES.copies(thread.runtime):
        try:
            await runtime.update(thread.thread_id, _FLAG)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Copying the rollout check to its runtime failed",
                extra={"thread_id": thread.thread_id},
                exc_info=True,
            )


async def request_rollout_check() -> dict[str, object]:
    """Implement the `request_rollout_check` tool."""
    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}
    thread = await index.get(thread_id)
    if thread is None:
        return {"success": False, "error": "Thread was not found"}
    updated = await index.update(thread_id, _FLAG)
    if updated is None:
        return {"success": False, "error": "Thread was not found"}
    await _copy_flag(updated)
    metadata: dict[str, object] = dict(updated.metadata)
    identity = merged_pull_request(metadata)
    if identity is None:
        return {"success": True, "rollout_check": True, "subscribed": False}
    owner, repo, number, sha = identity
    await subscribe_merged_thread(
        thread_id,
        owner=owner,
        repo=repo,
        number=number,
        sha=sha,
        metadata=metadata,
    )
    return {"success": True, "rollout_check": True, "subscribed": True}
