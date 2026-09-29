"""Tool: ``approve_pull_request``. Approves the pull request on GitHub as the person asking."""

from typing import Any

from agent.review_guide import git
from agent.review_guide.context import GuideContext, GuideUnavailableError
from agent.review_guide.github import approve
from agent.run_config import RunConfig
from agent.users import User

DEFAULT_BODY = "Approved after a guided walkthrough in Slack via Open SWE."


async def approve_pull_request(body: str = "") -> dict[str, Any]:
    """Implement the `approve_pull_request` tool."""
    cfg = RunConfig.from_runtime()
    slack_user_id = cfg.slack_thread.triggering_user_id if cfg.slack_thread else ""
    if not slack_user_id:
        return {
            "success": False,
            "error": "only a person's own message can approve; ask them to confirm first",
        }
    user = await User.for_identity("slack", slack_user_id)
    login = user.github_login if user else ""
    if not login:
        return {
            "success": False,
            "error": "the person asking has no GitHub account linked to Open SWE",
        }
    try:
        ctx = await GuideContext.current()
        built = await git.built_for(ctx.backend, ctx.repo_dir)
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
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
