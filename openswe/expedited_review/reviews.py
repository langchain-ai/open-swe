"""The GitHub review behind each expedited approval, from the click until the card closes."""

import logging

import httpx2

from openswe.github.http import GitHubClient, GitHubError, GitHubSignInRequired
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from openswe.slack.client import get_slack_permalink
from openswe.utils.dashboard_links import dashboard_base_url

logger = logging.getLogger(__name__)


REVIEW_BODY_PREFIX = "Approved in Slack via"


def settings_hint(action: str, path: str = "/my-settings") -> str:
    base = dashboard_base_url()
    return f"{action}: {base}{path}" if base else f"{action} in your Robin settings."


def github_token_hint() -> str:
    return settings_hint("Sign in again with GitHub to refresh Robin's access")


def github_error(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    message = body.get("message") if isinstance(body, dict) else None
    text = message if isinstance(message, str) and message else response.text[:200]
    return f"{response.status_code} {text}".strip()


async def _review_body(approval: HumanReviewRequest) -> str:
    # The card is reposted when it is broadcast or closed; the thread root is the stable link.
    anchor = (
        approval.slack_message_ts if approval.slack_thread_ts == "0" else approval.slack_thread_ts
    )
    permalink = await get_slack_permalink(approval.slack_channel_id, anchor)
    if permalink is None:
        return f"{REVIEW_BODY_PREFIX} Robin expedited review."
    return f"{REVIEW_BODY_PREFIX} [expedited review]({permalink})."


async def submit_approval(
    approval: HumanReviewRequest, vote: HumanReviewParticipant, head_sha: str
) -> str | None:
    """POST ``vote`` as its voter's ``APPROVE`` review on ``head_sha``; why it failed, or ``None``.

    Records the review id and SHA on ``vote``; the caller persists them.
    """
    login = vote.github_login
    pr = approval.pull_request
    payload = {"commit_id": head_sha, "event": "APPROVE", "body": await _review_body(approval)}
    failed: str | None = None
    try:
        async with GitHubClient.as_user(login) as github:
            data = (
                await github.repo(pr.owner, pr.repo).pull_request(pr.number).create_review(payload)
            )
    except GitHubSignInRequired:
        failed = f"Robin has no GitHub token for @{login}. {github_token_hint()}"
    except httpx2.HTTPStatusError as exc:
        failed = f"GitHub rejected @{login}'s review: {github_error(exc.response)}"
    except httpx2.HTTPError, ValueError:
        failed = f"GitHub did not answer when submitting @{login}'s review."
    else:
        review_id = data.get("id") if isinstance(data, dict) else None
        if isinstance(review_id, int):
            vote.github_review_id = review_id
            vote.github_review_sha = head_sha
        else:
            failed = f"GitHub returned an unexpected review response for @{login}."
    await approval.log_event(
        "github.approval_submitted",
        user_id=str(vote.user_id),
        github_login=login,
        head_sha=head_sha,
        github_review_id=vote.github_review_id,
        error=failed,
    )
    return failed


async def dismiss_approval(
    approval: HumanReviewRequest, vote: HumanReviewParticipant, pull: PullRequestClient, reason: str
) -> None:
    """Withdraw the GitHub review ``vote`` submitted; clears its id once GitHub confirms.

    A review whose id is still set after this is retried the next time a card closes.
    """
    if vote.github_review_id is None:
        return
    review_id = vote.github_review_id
    try:
        await pull.dismiss_review(review_id, f"Expedited review closed: {reason}")
    except GitHubError as refused:
        # A review GitHub no longer has cannot count toward a merge either.
        if refused.response.status_code == 404:
            vote.github_review_id = None
        else:
            logger.warning(
                "GitHub refused to dismiss an expedited review approval",
                extra={
                    "approval_id": str(approval.id),
                    "github_review_id": review_id,
                    "github_message": refused.message,
                },
            )
    except httpx2.HTTPError:
        logger.warning(
            "Failed to dismiss an expedited review approval on GitHub",
            extra={"approval_id": str(approval.id), "github_review_id": review_id},
            exc_info=True,
        )
    else:
        vote.github_review_id = None
    await approval.log_event(
        "github.approval_dismissed",
        user_id=str(vote.user_id),
        github_login=vote.github_login,
        github_review_id=review_id,
        reason=reason,
        dismissed=vote.github_review_id is None,
    )
