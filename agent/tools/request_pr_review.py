from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

from agent.review.dispatch import trigger_pr_review_from_ref
from agent.utils.slack import get_active_slack_thread, parse_github_pr_url


async def request_pr_review(pr_url: str) -> dict[str, Any]:
    """Start the reviewer agent for a GitHub pull request URL."""
    pr_ref = parse_github_pr_url(pr_url)
    if not pr_ref:
        return {
            "success": False,
            "error": "Expected a GitHub PR URL like https://github.com/OWNER/REPO/pull/NUMBER",
        }

    configurable = get_config().get("configurable", {})
    source = configurable.get("source") or "agent"
    slack_thread = configurable.get("slack_thread") or {}
    thread_id = configurable.get("thread_id")
    active = await get_active_slack_thread(
        get_client(),
        thread_id if isinstance(thread_id, str) else None,
        slack_thread if isinstance(slack_thread, dict) else None,
    )
    slack_thread = active or {}
    return await trigger_pr_review_from_ref(
        pr_ref,
        source=source,
        github_login=configurable.get("github_login", ""),
        github_user_id=configurable.get("github_user_id"),
        slack_channel_id=slack_thread.get("channel_id", ""),
        slack_thread_ts=slack_thread.get("thread_ts", ""),
    )
