"""Report stale workspace images to operators without interrupting users."""

from datetime import UTC, datetime, timedelta

from openswe.slack.alerts import alerts
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.time import parse_iso
from openswe.workspaces.refresh import is_snapshot_stale
from openswe.workspaces.store import Workspace


class StaleWorkspaceReporter:
    @staticmethod
    async def report(thread_id: str, workspace: Workspace) -> None:
        if not is_snapshot_stale(workspace, interval_seconds=2 * 60 * 60):
            return
        captured = parse_iso(workspace.last_captured_at)
        image_age = "unknown (capture timestamp missing or invalid)"
        if captured is not None:
            hours, minutes = divmod(int((datetime.now(UTC) - captured).total_seconds()) // 60, 60)
            days, hours = divmod(hours, 24)
            image_age = f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"
        await alerts.warning(
            "Run booted a stale workspace image",
            slack_message=f"Run booted a stale workspace image (age: {image_age})",
            details={
                "Workspace": workspace.slug,
                "Image age": image_age,
                "Run": dashboard_thread_url(thread_id) or thread_id,
                "Image captured": workspace.last_captured_at,
                "Refresh status": workspace.refresh_status,
                "Refresh run": workspace.refresh_run_id,
                "Refresh started": workspace.refresh_started_at,
                "Refresh finished": workspace.refresh_finished_at,
            },
            cooldown=timedelta(hours=8),
        )
