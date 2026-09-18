"""Tool for explicitly rebinding the current thread to a fresh sandbox."""

import logging
from typing import Any

from agent.run_config import RunConfig
from agent.sandboxes.lifecycle import SandboxSource
from agent.tools.admin_gate import require_private_admin_surface

logger = logging.getLogger(__name__)


async def recreate_sandbox(
    source: SandboxSource = "workspace",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Implement the `recreate_sandbox` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}

    from agent.server import workspace_slug

    thread_workspace = workspace_slug(cfg)
    if workspace is not None and workspace != thread_workspace:
        if error := await require_private_admin_surface("boot another workspace's sandbox image"):
            return {"success": False, "error": error}

    try:
        from agent.sandboxes.lifecycle import recreate_sandbox_for_thread

        old_sandbox_id, new_sandbox_id = await recreate_sandbox_for_thread(
            thread_id,
            workspace_slug=workspace or thread_workspace,
            source=source,
        )
    except Exception as exc:
        logger.exception("Failed to recreate sandbox for thread %s", thread_id)
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "old_sandbox_id": old_sandbox_id,
        "new_sandbox_id": new_sandbox_id,
    }
