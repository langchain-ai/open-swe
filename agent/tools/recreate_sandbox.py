"""Tool for explicitly rebinding the current thread to a fresh sandbox."""

import logging
from typing import Any

from langgraph.config import get_config

from agent.run_config import RunConfig

logger = logging.getLogger(__name__)


async def recreate_sandbox() -> dict[str, Any]:
    """Rebind this thread to a fresh sandbox.

    The fresh sandbox has none of the thread's current files or worktree state.
    The old sandbox is not deleted, but it becomes inaccessible from this thread
    after the handoff.

    Returns ``success``, ``old_sandbox_id``, and ``new_sandbox_id`` on success.
    """
    try:
        config = get_config()
    except Exception as exc:
        return {"success": False, "error": f"Unable to read the current run config: {exc}"}

    cfg = RunConfig.from_config(config)
    thread_id = cfg.thread_id
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}

    try:
        from agent.sandboxes.lifecycle import recreate_sandbox_for_thread
        from agent.server import _environment_slug

        old_sandbox_id, new_sandbox_id = await recreate_sandbox_for_thread(
            thread_id,
            environment_slug=_environment_slug(cfg),
        )
    except Exception as exc:
        logger.exception("Failed to recreate sandbox for thread %s", thread_id)
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "old_sandbox_id": old_sandbox_id,
        "new_sandbox_id": new_sandbox_id,
    }
