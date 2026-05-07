import asyncio
from typing import Any

from agent.utils.slack import parse_github_pr_url
from agent.webapp import trigger_pr_review_from_ref


def request_pr_review(pr_url: str) -> dict[str, Any]:
    """Start the reviewer agent for a GitHub pull request URL."""
    pr_ref = parse_github_pr_url(pr_url)
    if not pr_ref:
        return {
            "success": False,
            "error": "Expected a GitHub PR URL like https://github.com/OWNER/REPO/pull/NUMBER",
        }

    return asyncio.run(trigger_pr_review_from_ref(pr_ref, source="slack"))
