import asyncio
from typing import Any

from agent.utils.gitlab import parse_gitlab_mr_url
from agent.webapp import trigger_mr_review_from_ref


def request_mr_review(mr_url: str) -> dict[str, Any]:
    """Start the reviewer agent for a GitLab merge request URL.

    Args:
        mr_url: Full GitLab MR URL, e.g.
            https://gitlab.com/OWNER/REPO/-/merge_requests/123

    Returns:
        Dict with 'success' or 'error' key.
    """
    mr_ref = parse_gitlab_mr_url(mr_url)
    if not mr_ref:
        return {
            "success": False,
            "error": "Expected a GitLab MR URL like "
            "https://gitlab.com/OWNER/REPO/-/merge_requests/NUMBER",
        }

    return asyncio.run(trigger_mr_review_from_ref(mr_ref, source="slack"))
