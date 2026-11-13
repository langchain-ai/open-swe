from fastapi import HTTPException

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.approval_policy import PullRequestApproval
from openswe.run_config import RunConfig
from openswe.slack.client import parse_github_pr_url
from openswe.tools.mcp_exposure import expose_mcp


@expose_mcp()
@audit_tool()
async def recheck_pr_approval(pr_url: str) -> dict[str, object]:
    """Implement the `recheck_pr_approval` tool."""
    pr = parse_github_pr_url(pr_url)
    if pr is None:
        return {"success": False, "error": "Expected a GitHub pull request URL"}
    cfg = RunConfig.from_runtime()
    login = cfg.mcp_caller or cfg.github_login
    if not login:
        return {"success": False, "error": "A linked GitHub identity is required"}
    try:
        await require_repo_access_for_user(login, f"{pr.owner}/{pr.repo}")
    except HTTPException as exc:
        return {"success": False, "error": str(exc.detail)}
    result = await PullRequestApproval.evaluate(pr.owner, pr.repo, pr.number, force_publish=True)
    if result is None:
        return {
            "success": False,
            "published": False,
            "error": "Approval publishing is disabled, the pull request is closed, or its base branch or head SHA is unavailable",
        }
    passed, summary = result
    return {"success": True, "published": True, "passed": passed, "summary": summary}
