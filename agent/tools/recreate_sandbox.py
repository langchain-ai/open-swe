"""Tool for explicitly rebinding the current thread to a fresh sandbox."""

import logging
from typing import Any

from langgraph.config import get_config

from ..runtime.sandbox import environment_slug, recreate_sandbox_for_thread, resolve_default_repo

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

    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}

    try:
        repo = await resolve_default_repo(configurable)
        old_sandbox_id, new_sandbox_id = await recreate_sandbox_for_thread(
            thread_id,
            repo=repo,
            environment_slug=environment_slug(configurable),
        )
    except Exception as exc:
        logger.exception("Failed to recreate sandbox for thread %s", thread_id)
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "old_sandbox_id": old_sandbox_id,
        "new_sandbox_id": new_sandbox_id,
    }
