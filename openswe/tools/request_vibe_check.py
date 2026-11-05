import logging

from openswe.audit_logs.tools import audit_tool
from openswe.github.http import GitHubAppUnavailable
from openswe.review.vibe_check import VibeCheck
from openswe.slack.client import parse_github_pr_url
from openswe.tools.mcp_exposure import expose_mcp

logger = logging.getLogger(__name__)


@expose_mcp()
@audit_tool()
async def request_vibe_check(pr_url: str) -> dict[str, object]:
    """Implement the `request_vibe_check` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if not pr_ref:
        return {
            "success": False,
            "error": "Expected a GitHub PR URL like https://github.com/OWNER/REPO/pull/NUMBER",
        }
    try:
        check = await VibeCheck.run_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    except GitHubAppUnavailable as exc:
        return {"success": False, "error": f"The Open SWE GitHub App cannot reach the repo: {exc}"}
    except Exception as exc:
        logger.exception("Vibe check failed", extra={"pr_url": pr_url})
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "success": True,
        "head_sha": check.head_sha,
        "files_checked": check.files_checked,
        "passed": not check.errors,
        "comment_url": check.comment_url,
        "verdicts": [
            {
                "path": v.path,
                "rule": v.rule.id,
                "level": v.level,
                "probability": round(v.probability, 3),
                "why": v.rule.violation,
            }
            for v in check.verdicts
        ],
    }
