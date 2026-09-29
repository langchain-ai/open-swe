"""Tool: ``approve_pull_request``. Approves the pull request on GitHub as the person asking."""

from typing import Any

from agent.review_guide import git
from agent.review_guide.context import GuideContext, GuideUnavailableError, requester_login
from agent.review_guide.github import approve

DEFAULT_BODY = "Approved after a guided walkthrough in Slack via Open SWE."


async def approve_pull_request(body: str = "") -> dict[str, Any]:
    """Implement the `approve_pull_request` tool."""
    try:
        login = await requester_login()
        ctx = await GuideContext.current()
        built = await git.built_for(ctx.backend, ctx.repo_dir)
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    if ctx.session.mode == "author":
        return {"success": False, "error": "the author cannot approve their own pull request"}
    if built is None:
        return {"success": False, "error": "the checkout is not ready; try again next turn"}
    pr = ctx.session.pull_request
    error = await approve(
        login=login,
        owner=pr.owner,
        repo=pr.repo,
        number=pr.number,
        head_sha=built[1],
        body=body.strip() or DEFAULT_BODY,
    )
    if error:
        return {"success": False, "error": error}
    return {"success": True, "approved_as": login, "head_sha": built[1]}
