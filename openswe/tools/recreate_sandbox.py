"""Tool for explicitly rebinding the current thread to a fresh sandbox."""

import logging
from collections.abc import Mapping
from typing import NotRequired, TypedDict

from openswe.audit_logs.tools import audit_tool
from openswe.run_config import RunConfig
from openswe.sandboxes.lifecycle import SandboxRecreationStopError, SandboxSource
from openswe.tools.access import Policy, access

logger = logging.getLogger(__name__)


class SandboxRecreationResult(TypedDict):
    success: bool
    error: NotRequired[str]
    old_sandbox_id: NotRequired[str]
    new_sandbox_id: NotRequired[str]
    old_sandbox_stopped: NotRequired[bool]
    old_sandbox_stop_error: NotRequired[str | None]


def _switches_workspace(args: Mapping[str, object]) -> Policy | None:
    workspace = args.get("workspace")
    if workspace is None or workspace == RunConfig.from_runtime().workspace_slug:
        return None
    return Policy(trusted="admin_surface", actor="admin")


@audit_tool()
@access(Policy(trusted="anywhere"), per_call=_switches_workspace)
async def recreate_sandbox(
    source: SandboxSource = "workspace",
    workspace: str | None = None,
) -> SandboxRecreationResult:
    """Implement the `recreate_sandbox` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}

    from openswe.server import workspace_slug

    thread_workspace = workspace_slug(cfg)
    try:
        from openswe.sandboxes.lifecycle import recreate_sandbox_for_thread

        old_sandbox_id, new_sandbox_id = await recreate_sandbox_for_thread(
            thread_id,
            workspace_slug=workspace or thread_workspace,
            source=source,
        )
    except SandboxRecreationStopError as exc:
        logger.warning("Sandbox recreated but old sandbox did not stop", exc_info=True)
        return {
            "success": True,
            "old_sandbox_id": exc.old_sandbox_id,
            "new_sandbox_id": exc.new_sandbox_id,
            "old_sandbox_stopped": False,
            "old_sandbox_stop_error": str(exc),
        }
    except Exception as exc:
        logger.exception("Failed to recreate sandbox", extra={"thread_id": thread_id})
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "old_sandbox_id": old_sandbox_id,
        "new_sandbox_id": new_sandbox_id,
        "old_sandbox_stopped": True,
        "old_sandbox_stop_error": None,
    }
