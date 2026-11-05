"""Report stale workspace images to operators without interrupting users."""

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
                "workspace": workspace.slug,
                "agent_thread_id": thread_id,
                "thread_url": dashboard_thread_url(thread_id),
                "captured_at": workspace.last_captured_at,
                "refresh_status": workspace.refresh_status,
                "refresh_run_id": workspace.refresh_run_id,
                "refresh_started_at": workspace.refresh_started_at,
                "refresh_finished_at": workspace.refresh_finished_at,
            },
        )
