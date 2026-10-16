"""Tool: ``approve_pull_request``. Approves the pull request on GitHub as the person asking."""

from typing import Any

from openswe.audit_logs.tools import audit_tool
from openswe.review_guide.context import GuideContext, GuideUnavailableError, requester_login
from openswe.review_guide.github import approve

DEFAULT_BODY = "Approved after a guided walkthrough in Slack via Open SWE."


@audit_tool()
async def approve_pull_request(body: str = "") -> dict[str, Any]:
    """Implement the `approve_pull_request` tool."""
    try:
        login = await requester_login()
        ctx = await GuideContext.current()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    if ctx.session.mode == "author":
        return {"success": False, "error": "the author cannot approve their own pull request"}
    walk = ctx.session.walk
    coverage = walk.coverage() if walk and walk.head_sha == ctx.head_sha else ""
    pr = ctx.session.pull_request
    error = await approve(
        login=login,
        owner=pr.owner,
        repo=pr.repo,
        number=pr.number,
        head_sha=ctx.head_sha,
        body="\n\n".join(part for part in (body.strip() or DEFAULT_BODY, coverage) if part),
    )
    if error:
        return {"success": False, "error": error}
    return {"success": True, "approved_as": login, "head_sha": ctx.head_sha}
