"""Show stale workspace image warnings on the dashboard."""

import logging

from openswe.middleware.transcript import queue_run_notice
from openswe.transcript.events import JsonObject
from openswe.workspaces.refresh import is_snapshot_stale
from openswe.workspaces.store import Workspace

logger = logging.getLogger(__name__)


def stale_workspace_notice(thread_id: str, workspace: Workspace) -> JsonObject | None:
    """The dashboard warning's data when the workspace image is stale; None when it is not."""
    if not is_snapshot_stale(workspace, interval_seconds=2 * 60 * 60):
        return None
    logger.info(
        "Run booted a stale workspace image",
        extra={
            "workspace": workspace.slug,
            "agent_thread_id": thread_id,
            "captured_at": workspace.last_captured_at,
            "refresh_status": workspace.refresh_status,
            "refresh_run_id": workspace.refresh_run_id,
            "refresh_started_at": workspace.refresh_started_at,
            "refresh_finished_at": workspace.refresh_finished_at,
        },
    )
    return {
        "workspace": workspace.slug,
        "workspace_name": workspace.name or workspace.slug,
        "captured_at": workspace.last_captured_at,
    }


def queue_stale_workspace_notice(thread_id: str, workspace: Workspace) -> None:
    """Queue the dashboard warning when the workspace image is stale."""
    if (data := stale_workspace_notice(thread_id, workspace)) is not None:
        queue_run_notice(thread_id, "workspace_stale", data)
