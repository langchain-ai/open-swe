"""Tool for explicitly giving the current thread a fresh sandbox, which MDA creates."""

import logging
from collections.abc import Mapping
from typing import NotRequired, TypedDict

from pydantic import JsonValue

from openswe.audit_logs.tools import audit_tool
from openswe.run_config import RunConfig
from openswe.sandboxes.lifecycle import (
    SandboxSource,
    hand_sandbox_to_mda,
    request_mda_replacement,
)
from openswe.sandboxes.state import get_sandbox_metadata
from openswe.tasks.schemas import ThreadMetadata
from openswe.tools.access import Policy, access

logger = logging.getLogger(__name__)


class SandboxRecreationResult(TypedDict):
    success: bool
    error: NotRequired[str]
    old_sandbox_id: NotRequired[str]
    old_sandbox_stopped: NotRequired[bool]
    old_sandbox_stop_error: NotRequired[str | None]
    # A box the backend made: the thread's next run starts in a fresh one MDA creates.
    handed_to_mda: NotRequired[bool]
    # An MDA-owned box: what MDA replaces it with; MDA answers with the rest.
    replace: NotRequired[dict[str, JsonValue]]


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
    metadata = ThreadMetadata.model_validate(await get_sandbox_metadata(thread_id))
    if metadata.sandbox_owner == "mda" and metadata.task_id is None:
        # MDA names and replaces this box; the call's access policy has already run.
        options = await request_mda_replacement(
            thread_id, workspace_slug=workspace or thread_workspace, source=source
        )
        return {"success": True, "replace": options.model_dump(mode="json")}
    try:
        handed = await hand_sandbox_to_mda(
            thread_id, workspace_slug=workspace or thread_workspace, source=source
        )
    except Exception as exc:
        logger.exception("Failed to recreate sandbox", extra={"thread_id": thread_id})
        return {"success": False, "error": str(exc)}
    return {
        "success": True,
        "old_sandbox_id": handed.old_sandbox_id,
        "old_sandbox_stopped": handed.stop_error is None,
        "old_sandbox_stop_error": handed.stop_error,
        "handed_to_mda": True,
    }
