"""Show stale workspace image warnings on the dashboard."""

import logging

from openswe.middleware.transcript import queue_run_notice
from openswe.workspaces.refresh import is_snapshot_stale
from openswe.workspaces.store import Workspace

logger = logging.getLogger(__name__)


def queue_stale_workspace_notice(thread_id: str, workspace: Workspace) -> None:
    """Queue the dashboard warning when the workspace image is stale."""
    if not is_snapshot_stale(workspace, interval_seconds=2 * 60 * 60):
        return
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
    queue_run_notice(
        thread_id,
        "workspace_stale",
        {
            "workspace": workspace.slug,
            "workspace_name": workspace.name or workspace.slug,
            "captured_at": workspace.last_captured_at,
        },
    )
