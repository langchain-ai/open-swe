"""DM an admin's announcement to everyone, once they have confirmed the exact message."""

from openswe.announcements.delivery import start_announcement
from openswe.announcements.models import TEXT_MAX_CHARS, Announcement
from openswe.audit_logs.tools import audit_tool
from openswe.run_config import RunConfig
from openswe.tools.access import Policy, access
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User


@expose_mcp(access="admin")
@audit_tool()
@access(Policy(trusted="admin_surface", actor="admin", direct=True))
async def send_announcement(text: str, confirm: bool = False) -> dict[str, object]:
    """Preview an announcement DM to every Slack-linked user, or send it with confirm=true."""
    text = text.strip()
    if not text:
        raise ValueError("The announcement text is empty.")
    if len(text) > TEXT_MAX_CHARS:
        raise ValueError(f"Keep the announcement under {TEXT_MAX_CHARS} characters.")
    if not confirm:
        return {
            "status": "preview",
            "recipients": await Announcement.recipient_count(),
            "text": text,
        }
    author = await _author(configurable())
    recipients = await start_announcement(text, author.id if author else None)
    return {"status": "sending", "recipients": recipients}


async def _author(cfg: RunConfig) -> User | None:
    if cfg.github_login:
        return await User.for_login("github", cfg.github_login)
    slack = cfg.slack_thread
    if slack is not None and slack.triggering_user_id:
        return await User.for_identity("slack", slack.triggering_user_id)
    return None
