"""Prepare an announcement for a human admin to approve."""

from openswe.announcements.authorization import ApprovalAuthorization
from openswe.announcements.models import TEXT_MAX_CHARS, AnnouncementApproval
from openswe.audit_logs.tools import audit_tool
from openswe.tools.access import Policy, access
from openswe.tools.admin_gate import configurable
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_base_url


@audit_tool()
@access(Policy(trusted="admin_surface", actor="admin", direct=True))
async def send_announcement(text: str) -> dict[str, object]:
    """Prepare a broadcast for the thread owner to review and approve in the dashboard."""
    text = text.strip()
    if not text:
        raise ValueError("The announcement text is empty.")
    if len(text) > TEXT_MAX_CHARS:
        raise ValueError(f"Keep the announcement under {TEXT_MAX_CHARS} characters.")
    cfg = configurable()
    if cfg.mcp_caller or not cfg.thread_id or not cfg.github_login:
        raise PermissionError("An owned private admin thread is required.")
    author = (
        await User.for_identity("github", cfg.github_user_id)
        if cfg.github_user_id
        else await User.for_login("github", cfg.github_login)
    )
    if author is None or not await ApprovalAuthorization.permits(cfg.thread_id, author.id):
        raise PermissionError(
            "Only the owner of a private admin thread may prepare an announcement."
        )
    base = dashboard_base_url()
    if not base:
        raise ValueError("A dashboard URL is required to review the announcement.")
    approval = await AnnouncementApproval.prepare(cfg.thread_id, author.id, text)
    return {
        "status": "pending_approval",
        "recipients": approval.recipients,
        "text": approval.text,
        "approval_url": f"{base}/admin/users#announcement-approvals",
        "expires_at": approval.expires_at.isoformat(),
    }
