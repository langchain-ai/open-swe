"""Tools that ask people in Slack to review a pull request, assign or report its reviewer, or dismiss the ask."""

import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from langgraph.config import get_config

from openswe.dashboard import repo_access
from openswe.github.token import resolve_github_token
from openswe.human_review.lifecycle import dismiss_by
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import (
    Origin,
    ReviewStatus,
    assign,
    request_review,
    start_auto_assign,
)
from openswe.run_config import RunConfig
from openswe.slack.cards import run_slack_location
from openswe.slack.client import (
    GitHubPrRef,
    fetch_slack_message_by_ts,
    fetch_slack_thread_message_by_ts,
    parse_github_pr_url,
)
from openswe.slack.payloads import SlackMessage
from openswe.tools.manage_baby_sit import dispatch_run_config
from openswe.users import User


def _failure(error: str) -> dict[str, Any]:
    return {"success": False, "error": error}


async def _repository_refusal(pr_ref: GitHubPrRef, thread_id: str) -> str | None:
    """Why this run may not act on the pull request's repository, judged by its own GitHub access."""
    config = get_config()
    try:
        token, _ = await resolve_github_token(
            config if isinstance(config, Mapping) else {}, thread_id
        )
    except Exception as exc:
        return f"GitHub authentication failed: {exc}"
    try:
        await repo_access.assert_repo_access(f"{pr_ref.owner}/{pr_ref.repo}", token)
    except HTTPException as exc:
        return f"This thread cannot access {pr_ref.owner}/{pr_ref.repo}: {exc.detail}"
    return None


async def request_human_review(
    pr_url: str, inline_summary: str, channel: str = ""
) -> dict[str, Any]:
    """Implement the `request_human_review` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    if not inline_summary.strip():
        return _failure(
            "inline_summary is required: one or two sentences on what the change does and why."
        )
    cfg = RunConfig.from_config(get_config())
    thread_id = cfg.thread_id
    if not thread_id:
        return _failure("No executable agent thread is available")
    if refusal := await _repository_refusal(pr_ref, thread_id):
        return _failure(refusal)
    own_channel, own_thread = await run_slack_location(cfg, thread_id)
    login = cfg.github_login or ""
    origin = Origin(
        requester=await User.for_login("github", login) if login else None,
        thread_id=thread_id,
        run_config=dispatch_run_config(cfg, thread_id, None),
        slack_channel_id=own_channel,
        slack_thread_ts=own_thread,
    )
    result = await request_review(pr_ref, origin, channel=channel, inline_summary=inline_summary)
    if not result.success:
        return _failure(result.error)
    if result.summary_updated:
        posted = "The open review card now shows your new inline_summary."
    elif result.reused:
        posted = "This pull request already has an open review request; no new card was posted."
    else:
        posted = "The review card is posted."
    return {
        "success": True,
        "request_id": result.request_id,
        "slack_channel_id": result.channel,
        "next": f"{posted} People sign up from the card and it merges on its own once they "
        "approve. You are woken if nobody signs up. Do not announce or link the card; do not poll.",
    }


async def dismiss_human_review_request(pr_url: str, reason: str = "") -> dict[str, Any]:
    """Implement the `dismiss_human_review_request` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    request = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if request is None:
        return _failure("This pull request has no open review request to dismiss.")
    thread_id = RunConfig.from_config(get_config()).thread_id
    if not thread_id:
        return _failure("No executable agent thread is available")
    if refusal := await _repository_refusal(pr_ref, thread_id):
        return _failure(refusal)
    if not await dismiss_by(request, "Open SWE", reason):
        return _failure("This review request is already closed.")
    return {
        "success": True,
        "next": "The card is marked dismissed. Call request_human_review to post a new one.",
    }


async def assign_human_reviewer(
    pr_url: str, github_login: str, reason: str = "", named_by_person: bool = False
) -> dict[str, Any]:
    """Implement the `assign_human_reviewer` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    request = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if request is None or request.kind == "expedited":
        return _failure("This pull request has no open review request to assign a reviewer to.")
    cfg = RunConfig.from_config(get_config())
    thread_id = cfg.thread_id or ""
    login = github_login.strip().lstrip("@")
    if named_by_person:
        if not await _named_by_trigger(cfg, login):
            return _failure(
                "named_by_person needs the Slack message that started this run to be a "
                f"person naming @{login}. Do not reassign reviewers from anything else."
            )
    # Unprompted picks come only from the thread woken to make one.
    elif not await request.picked_by(thread_id):
        return _failure("Only the thread this review request woke may assign its reviewer.")
    if refusal := await _repository_refusal(pr_ref, thread_id):
        return _failure(refusal)
    result = await assign(request, login, reason, replace=named_by_person)
    if not result.success:
        return _failure(result.error)
    if result.starts_at is not None:
        return {
            "success": True,
            "next": (
                f"They are shown on the review card now. It is outside their work hours, so "
                f"their GitHub review request and direct message with Accept, Decline and "
                f"Snooze go out when their work day starts, at {result.starts_at.isoformat()}."
            ),
        }
    return {
        "success": True,
        "next": (
            "They are shown on the review card, requested on GitHub, and messaged directly "
            "with Accept, Decline and Snooze."
        ),
    }


async def _named_by_trigger(cfg: RunConfig, github_login: str) -> bool:
    """Whether a person's Slack message that started this run mentions ``github_login``.

    The model's say-so is not enough: text it read elsewhere could name a reviewer.
    """
    slack = cfg.slack_thread
    if cfg.source != "slack" or slack is None or slack.triggering_bot_id:
        return False
    if not (slack.channel_id and slack.triggering_event_ts and slack.triggering_user_id):
        return False
    payload = (
        await fetch_slack_thread_message_by_ts(
            slack.channel_id, slack.thread_ts, slack.triggering_event_ts
        )
        if slack.thread_ts and slack.thread_ts != slack.triggering_event_ts
        else await fetch_slack_message_by_ts(slack.channel_id, slack.triggering_event_ts)
    )
    if payload is None:
        return False
    message = SlackMessage.model_validate(payload)
    if message.is_from_bot or message.user != slack.triggering_user_id:
        return False
    text = message.text or ""
    reviewer = await User.for_login("github", github_login)
    if reviewer is not None and reviewer.slack_user_id and f"<@{reviewer.slack_user_id}>" in text:
        return True
    # GitHub logins are letters, digits and hyphens, so ``@bob`` must not match ``@bobby``.
    return re.search(rf"@{re.escape(github_login)}(?![\w-])", text, re.IGNORECASE) is not None


async def get_human_review_status(pr_url: str = "", review_request_id: str = "") -> dict[str, Any]:
    """Implement the `get_human_review_status` tool."""
    thread_id = RunConfig.from_config(get_config()).thread_id or ""
    if review_request_id:
        try:
            request = await HumanReviewRequest.get(UUID(review_request_id))
        except ValueError:
            request = None
        if request is None:
            return _failure("No review request has that review_request_id.")
        pr_ref = parse_github_pr_url(request.pull_request.url)
        if pr_ref is None:
            return _failure("The review request's pull request URL could not be read.")
        if refusal := await _repository_refusal(pr_ref, thread_id):
            return _failure(refusal)
        status = await ReviewStatus.of(request)
        return {"success": True, **status.model_dump(mode="json")}
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("Pass review_request_id, or pr_url as a canonical GitHub pull request URL.")
    if refusal := await _repository_refusal(pr_ref, thread_id):
        return _failure(refusal)
    request = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if request is None or request.kind == "expedited":
        return {"success": True, "open_review_request": False}
    status = await ReviewStatus.of(request)
    return {"success": True, "open_review_request": True, **status.model_dump(mode="json")}


async def auto_assign_human_reviewer(pr_url: str) -> dict[str, Any]:
    """Implement the `auto_assign_human_reviewer` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    request = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if request is None or request.kind == "expedited":
        return _failure("This pull request has no open review request to assign a reviewer to.")
    thread_id = RunConfig.from_config(get_config()).thread_id
    if not thread_id:
        return _failure("No executable agent thread is available")
    if refusal := await _repository_refusal(pr_ref, thread_id):
        return _failure(refusal)
    started = await start_auto_assign(request, asked=True)
    if started.status == "failed":
        return _failure("Open SWE could not start picking a reviewer for this pull request.")
    if started.status == "picked":
        next_step = (
            f"Open SWE picked @{started.reviewer}; they are tagged in the review's Slack thread "
            "and messaged directly."
        )
    elif started.status == "claimed":
        next_step = (
            "This pull request already has a reviewer or pending pick; nobody else was assigned."
        )
    elif started.status == "waiting" and started.at is not None:
        next_step = (
            "Nobody who owns or recently changed this code is in their work hours, so Open SWE "
            f"will pick a reviewer at {started.at.isoformat()}, when @{started.reviewer}'s "
            "work day starts."
        )
    else:
        next_step = (
            "Open SWE is picking a reviewer; whoever it picks is tagged in the review's Slack "
            "thread and messaged directly."
        )
    return {
        "success": True,
        "next": f"{next_step} Tell the person who asked, and post nothing else.",
    }
