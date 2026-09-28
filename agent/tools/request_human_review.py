"""Tools that ask people in Slack to review a pull request, and assign one when nobody signs up."""

from typing import Any

from langgraph.config import get_config

from agent.human_review.requests import HumanReviewRequest
from agent.human_review.standard import Origin, assign, request_review
from agent.run_config import RunConfig
from agent.slack.cards import run_slack_location
from agent.slack.client import parse_github_pr_url
from agent.tools.manage_baby_sit import dispatch_run_config
from agent.users import User


def _failure(error: str) -> dict[str, Any]:
    return {"success": False, "error": error}


async def request_human_review(pr_url: str, channel: str = "") -> dict[str, Any]:
    """Implement the `request_human_review` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    cfg = RunConfig.from_config(get_config())
    thread_id = cfg.thread_id
    if not thread_id:
        return _failure("No executable agent thread is available")
    own_channel, own_thread = await run_slack_location(cfg, thread_id)
    login = cfg.github_login or ""
    origin = Origin(
        requester=await User.for_login("github", login) if login else None,
        thread_id=thread_id,
        run_config=dispatch_run_config(cfg, thread_id, None),
        slack_channel_id=own_channel,
        slack_thread_ts=own_thread,
    )
    result = await request_review(pr_ref, origin, channel=channel)
    if not result.success:
        return _failure(result.error)
    posted = (
        "This pull request already has an open review request; no new card was posted."
        if result.reused
        else "The review card is posted."
    )
    return {
        "success": True,
        "request_id": result.request_id,
        "slack_channel_id": result.channel,
        "permalink": result.permalink,
        "next": f"{posted} People sign up from the card and it merges on its own once they "
        "approve. You are woken if nobody signs up. Link the card in your reply; do not poll.",
    }


async def assign_human_reviewer(pr_url: str, github_login: str, reason: str = "") -> dict[str, Any]:
    """Implement the `assign_human_reviewer` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    request = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if request is None or request.kind != "standard":
        return _failure("This pull request has no open review request to assign a reviewer to.")
    thread_id = RunConfig.from_config(get_config()).thread_id
    if request.thread_id and request.thread_id != thread_id:
        return _failure("This review request belongs to another agent thread.")
    result = await assign(request, github_login.strip().lstrip("@"), reason)
    if not result.success:
        return _failure(result.error)
    return {
        "success": True,
        "permalink": result.permalink,
        "next": "They are tagged on the card and messaged directly. Nothing else to post.",
    }
