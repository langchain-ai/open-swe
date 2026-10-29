"""Record that this thread should be checked after its pull request deploys."""

import logging

from pydantic import JsonValue

from openswe.rollout_events import (
    ROLLOUT_CHECK_CONTEXT,
    ROLLOUT_CHECK_REQUESTED,
    merged_pull_request,
    subscribe_merged_thread,
)
from openswe.run_config import RunConfig
from openswe.threads import index
from openswe.threads.index import IndexedThread
from openswe.threads.runtimes import RUNTIMES

logger = logging.getLogger(__name__)


def _flag(cfg: RunConfig) -> dict[str, JsonValue]:
    """Keep the requested Slack destination through a later Web handoff."""
    slack_thread = cfg.slack_thread.dump() if cfg.source == "slack" and cfg.slack_thread else None
    return {
        ROLLOUT_CHECK_REQUESTED: True,
        ROLLOUT_CHECK_CONTEXT: {
            "slack_thread": slack_thread,
        },
    }


async def _copy_flag(thread: IndexedThread, flag: dict[str, JsonValue]) -> None:
    """Copy the flag onto every runtime that keeps this thread. A failed copy stays in the index."""
    for runtime in RUNTIMES.copies(thread.runtime):
        try:
            await runtime.update(thread.thread_id, flag)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Copying the rollout check to its runtime failed",
                extra={"thread_id": thread.thread_id},
                exc_info=True,
            )


async def request_rollout_check() -> dict[str, object]:
    """Implement the `request_rollout_check` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}
    thread = await index.get(thread_id)
    if thread is None:
        return {"success": False, "error": "Thread was not found"}
    flag = _flag(cfg)
    updated = await index.update(thread_id, flag)
    if updated is None:
        return {"success": False, "error": "Thread was not found"}
    await _copy_flag(updated, flag)
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
