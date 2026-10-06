"""Telling the requester their sandbox booted from an out-of-date workspace image."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from agent.middleware.sandbox_circuit_breaker import get_slack_target
from agent.middleware.transcript import queue_run_notice
from agent.run_config import RunConfig
from agent.slack.client import post_slack_thread_reply
from agent.utils.user_messages import warning
from agent.workspaces.refresh import is_snapshot_stale
from agent.workspaces.store import Workspace

logger = logging.getLogger(__name__)


def _age(captured_at: str | None) -> str | None:
    if not captured_at:
        return None
    try:
        captured = datetime.fromisoformat(captured_at)
    except ValueError:
        logger.warning("Unparseable workspace capture time", extra={"captured_at": captured_at})
        return None
    hours = int((datetime.now(UTC) - captured).total_seconds() // 3600)
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{hours // 24} days"


def stale_workspace_message(workspace: Workspace) -> str:
    name = workspace.name or workspace.slug
    age = _age(workspace.last_captured_at)
    captured = f"was captured {age} ago" if age else "has never been refreshed"
    return warning(
        f"The {name} workspace image this sandbox started from {captured}, so its "
        "repositories may be out of date. Open SWE is continuing anyway and refreshing "
        "the image in the background."
    )


async def warn_stale_workspace(
    config: Mapping[str, Any], thread_id: str, workspace: Workspace
) -> None:
    """Warn on the dashboard, and in Slack when the run came from there. Never raises."""
    if not is_snapshot_stale(workspace, interval_seconds=2 * 60 * 60):
        return
    queue_run_notice(
        thread_id,
        "workspace_stale",
        {
            "workspace": workspace.slug,
            "workspace_name": workspace.name or workspace.slug,
            "captured_at": workspace.last_captured_at,
        },
    )
    try:
        slack_target = await get_slack_target(RunConfig.from_config(config))
        if slack_target is None:
            return
        channel_id, thread_ts = slack_target
        await post_slack_thread_reply(
            channel_id,
            thread_ts,
            stale_workspace_message(workspace),
            agent_thread_id=thread_id,
        )
    except Exception:
        logger.warning(
            "Could not post stale workspace warning to Slack",
            exc_info=True,
            extra={"workspace": workspace.slug},
        )
