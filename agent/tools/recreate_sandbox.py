"""Tool for explicitly rebinding the current thread to a fresh sandbox."""

import logging
from typing import Any

from agent.run_config import RunConfig

logger = logging.getLogger(__name__)


async def recreate_sandbox() -> dict[str, Any]:
    """Implement the `recreate_sandbox` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}

    try:
        from agent.sandboxes.lifecycle import recreate_sandbox_for_thread
        from agent.server import environment_slug

        old_sandbox_id, new_sandbox_id = await recreate_sandbox_for_thread(
            thread_id,
            environment_slug=environment_slug(cfg),
        )
    except Exception as exc:
        logger.exception("Failed to recreate sandbox for thread %s", thread_id)
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "old_sandbox_id": old_sandbox_id,
        "new_sandbox_id": new_sandbox_id,
    }
