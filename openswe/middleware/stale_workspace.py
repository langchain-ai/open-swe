"""Report stale workspace images to operators without interrupting users."""

from openswe.slack.alerts import alerts
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.workspaces.refresh import is_snapshot_stale
from openswe.workspaces.store import Workspace


class StaleWorkspaceReporter:
    def __init__(self, thread_id: str, workspace: Workspace) -> None:
        self.thread_id = thread_id
        self.workspace = workspace

    async def report(self) -> None:
        if not is_snapshot_stale(self.workspace, interval_seconds=2 * 60 * 60):
            return
        await alerts.warning(
            "Run booted a stale workspace image",
            details={
                "workspace": self.workspace.slug,
                "agent_thread_id": self.thread_id,
                "thread_url": dashboard_thread_url(self.thread_id),
                "captured_at": self.workspace.last_captured_at,
                "refresh_status": self.workspace.refresh_status,
                "refresh_run_id": self.workspace.refresh_run_id,
                "refresh_started_at": self.workspace.refresh_started_at,
                "refresh_finished_at": self.workspace.refresh_finished_at,
            },
        )
