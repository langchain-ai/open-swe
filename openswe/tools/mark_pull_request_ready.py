"""Tool: ``mark_pull_request_ready``. Takes the author's draft pull request out of draft."""

from typing import Any

from openswe.audit_logs.tools import audit_tool
from openswe.review_guide.context import GuideContext, GuideUnavailableError, requester_login
from openswe.review_guide.github import mark_ready


@audit_tool()
async def mark_pull_request_ready() -> dict[str, Any]:
    """Implement the `mark_pull_request_ready` tool."""
    try:
        login = await requester_login()
        ctx = await GuideContext.current()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    pr = ctx.session.pull_request
    error = await mark_ready(login=login, owner=pr.owner, repo=pr.repo, number=pr.number)
    if error:
        return {"success": False, "error": error}
    return {"success": True, "ready_for_review": True}
