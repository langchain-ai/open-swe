"""The agent tool that starts a slacklet of any kind in its own Slack thread."""

from typing import Any

from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.run_config import RunConfig
from openswe.slack.cards import run_slack_location
from openswe.slack.http import SlackRequestError
from openswe.slacklets.kind import SlackletRefused
from openswe.slacklets.kinds import KINDS
from openswe.slacklets.store import transcript
from openswe.users import User


@audit_tool()
async def start_slacklet(kind: str, subject: str) -> dict[str, Any]:
    """Implement the `start_slacklet` tool."""
    slacklet_kind = KINDS.get(kind)
    if slacklet_kind is None:
        return {"success": False, "error": f"kind must be one of: {', '.join(sorted(KINDS))}"}
    cfg = RunConfig.from_config(get_config())
    location = await run_slack_location(cfg, cfg.thread_id or "")
    if not all(location):
        return {"success": False, "error": "This run has no Slack thread to post the slacklet in"}
    user = await User.for_login("github", cfg.github_login) if cfg.github_login else None
    try:
        slacklet, entries = await slacklet_kind.begin(
            subject, cfg, location, created_by=user.id if user else None
        )
    except SlackletRefused as exc:
        return {"success": False, "error": str(exc)}
    except SlackRequestError as exc:
        return {"success": False, "error": f"Slack refused the slacklet message: {exc.code}"}
    return {
        "success": True,
        "slacklet_id": str(slacklet.id),
        "message_ts": slacklet.message_ts,
        "commands": transcript(entries),
    }
