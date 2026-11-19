"""Report stale execution images to operators without interrupting users."""

from datetime import UTC, datetime, timedelta

from openswe.execution import ExecutionProfile
from openswe.slack.alerts import alerts
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.time import parse_iso


class StaleExecutionReporter:
    @staticmethod
    async def report(thread_id: str, profile: ExecutionProfile) -> None:
        if not profile.stale():
            return
        image_age = StaleExecutionReporter.age(profile.captured_at)
        await alerts.warning(
            "Run booted a stale Robin-wide execution image",
            slack_message=f"Run booted a stale Robin-wide execution image (age: {image_age})",
            details={
                "Snapshot": profile.create.snapshot_id,
                "Image age": image_age,
                "Run": dashboard_thread_url(thread_id) or thread_id,
                "Image captured": profile.captured_at,
            },
            cooldown=timedelta(hours=8),
        )

    @staticmethod
    def age(captured_at: str | None) -> str:
        captured = parse_iso(captured_at)
        if captured is None:
            return "unknown (capture timestamp missing or invalid)"
        hours, minutes = divmod(int((datetime.now(UTC) - captured).total_seconds()) // 60, 60)
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"
