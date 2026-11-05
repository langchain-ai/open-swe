"""Report stale workspace images to operators without interrupting users."""

from datetime import timedelta

from openswe.slack.alerts import alerts
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.workspaces.refresh import is_snapshot_stale
from openswe.workspaces.store import Workspace


class StaleWorkspaceReporter:
    @staticmethod
    async def report(thread_id: str, workspace: Workspace) -> None:
        if not is_snapshot_stale(workspace, interval_seconds=2 * 60 * 60):
            return
        await alerts.warning(
            "Run booted a stale workspace image",
            details={
                "Workspace": workspace.slug,
                "Run": dashboard_thread_url(thread_id) or thread_id,
                "Image captured": workspace.last_captured_at,
                "Refresh status": workspace.refresh_status,
                "Refresh run": workspace.refresh_run_id,
                "Refresh started": workspace.refresh_started_at,
                "Refresh finished": workspace.refresh_finished_at,
            },
            cooldown=timedelta(hours=8),
        )
